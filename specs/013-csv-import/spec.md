<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: CSV import

**Feature Branch**: `40-csv-import`

**Created**: 2026-11-24

**Status**: Implemented, and live on the preview.

**Input**: Issue #40. Backlog feature 013: importing companies from a Comma-Separated Values (CSV) file, with a preview, a column mapping, a report of duplicates and errors, and an undo. It reuses the queue and the bucket of earlier features and adds no binding. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - Look at a file before importing it (Priority: P1)

A salesperson has a list of companies in a spreadsheet. They upload it and see its columns, a suggestion of where each column goes, and the first rows, before anything is stored.

**Independent Test**: `./preview-test.sh` sends a file with six columns to the preview endpoint and gets a suggestion for the name columns.

**Acceptance Scenarios**:

1. **Given** a CSV file with a header line,\
   **When** it is sent to `POST /imports/preview`,\
   **Then** the answer holds the headers, a suggested target for each header that is recognized, every target that may be chosen, the number of rows, and the first 10 rows, and nothing is stored.
2. **Given** columns named "First Name" and "Last Name",\
   **When** the file is previewed,\
   **Then** both are suggested for `contact.name`, because columns that share a target are joined.
3. **Given** a file that is empty, that has a column with no header, or that has two columns with the same header,\
   **When** it is previewed,\
   **Then** the answer is 400.
4. **Given** a file with more rows than the setting `import.max_rows`,\
   **When** it is previewed or imported,\
   **Then** the answer is 413.

### User Story 2 - Import the file (Priority: P1)

**Independent Test**: `./preview-test.sh` imports three rows and checks the company, its contact, and its fact.

**Acceptance Scenarios**:

1. **Given** a file and a mapping,\
   **When** they are sent to `POST /imports`,\
   **Then** the answer is 202 with the id of an `import_batch`, and the rows are imported on the queue.
2. **Given** a row with a company name or a website,\
   **When** it is imported,\
   **Then** a company is created as a `LEAD` with the source `CSV` and the id of the batch.
3. **Given** a row with a contact name or a contact email,\
   **When** it is imported,\
   **Then** the company gets that person as its primary contact.
4. **Given** a column that is mapped to `fact`,\
   **When** a row has a value in it,\
   **Then** the company gets a fact with the source `CSV`, whose key is the header in lowercase snake case and whose source detail is the file name.
5. **Given** a column that is left out of the mapping,\
   **When** the file is imported,\
   **Then** that column is ignored.

### User Story 3 - See what happened (Priority: P1)

**Acceptance Scenarios**:

1. **Given** an import that is still running,\
   **When** `GET /imports/{id}` is called,\
   **Then** the status is `RUNNING`, and the counts show how far it is.
2. **Given** a row whose website has the domain of a company that is not archived,\
   **When** the file is imported,\
   **Then** no company is created, and the row is listed as a duplicate with its row number, the domain, and the id of that company.
3. **Given** two rows of the file with the same domain,\
   **When** the file is imported,\
   **Then** the first creates a company, and the second is listed as a duplicate of the first row.
4. **Given** a row with no company name and no website, or with an employee count that is not a whole number,\
   **When** the file is imported,\
   **Then** the row is listed as an error with its row number and the reason, and the other rows are imported.
5. **Given** an import that could not be finished,\
   **When** `GET /imports/{id}` is called,\
   **Then** the status is `FAILED` with a message that is safe to show.

### User Story 4 - Undo an import (Priority: P2)

**Acceptance Scenarios**:

1. **Given** a finished import whose companies nobody has worked on,\
   **When** `DELETE /imports/{id}` is called,\
   **Then** those companies are deleted with their contacts and facts, and the answer says how many were deleted and how many were kept.
2. **Given** a company of the import that has been edited, moved to another stage, archived, or given a timeline entry, a task, a deal, an agreement, a document, an AI run, or a rubric answer,\
   **When** the import is undone,\
   **Then** that company is kept.
3. **Given** an import that is still running,\
   **When** it is undone,\
   **Then** the answer is 409.
4. **Given** a `MEMBER` who did not make the import,\
   **When** they undo it,\
   **Then** the answer is 403.

### Edge Cases

- Row numbers are those of a spreadsheet: the header line is row 1. A row with nothing in it is skipped and keeps its number.
- A file that starts with a byte order mark is read as if it had none. A quoted cell may hold commas and line breaks.
- A row that is shorter than the header line has no value for its last columns.
- A company with a name and no website has no domain, so it is never a duplicate.
- A contact title or phone with no name and no email makes no contact.
- A fact column whose key is a well-known yes-or-no or date key is stored in the form of that type. A date that is not understood makes the row an error.
- A row that is a duplicate only of an archived company is imported, as it would be when made by hand.
- A company that someone adds while the import runs, with the domain of a later row, makes that row a duplicate without a company id.
- An import that is delivered a second time, because the first attempt was cut off, is `FAILED` and creates nothing more. The companies of the first attempt stay, and undo removes them.

## Requirements

### Limits, checked on 2026-11-24

- A D1 `batch` counts as one query against the limit of queries for one invocation, however many statements it holds. Each statement of a batch has the limits of a single query.
- A queue consumer may run 15 minutes, and a message may hold 128 KB. The file is therefore not sent in the message.
- **Measured on the preview on 2026-11-24**: 2,000 rows, each with a company, a contact, and two facts (8,000 inserts), were imported in about 30 seconds, and undone in about 1 second.

### Functional Requirements

Reading the file:

- **FR-001**: Both endpoints that take a file take a JSON body whose `csv` is the text of the file. `POST /imports` also takes `mapping` and an optional `file_name`.
- **FR-002**: The first line holds the headers. Every column MUST have a header, and no two columns may share one.
- **FR-003**: A file with more rows than the setting `import.max_rows` is refused with 413. The setting is 2000, and 2000 is used when it is missing.

The mapping:

- **FR-004**: A mapping is an object from a header to a target. A target is `fact`, or one of: `company.name`, `company.website`, `company.phone`, `company.email`, `company.street`, `company.city`, `company.region`, `company.postal_code`, `company.country`, `company.industry`, `company.naics_code`, `company.employee_count`, `company.summary`, `contact.name`, `contact.title`, `contact.email`, `contact.phone`, `contact.notes`.
- **FR-005**: A header that is left out, or whose target is null or empty, is ignored. A header that is not in the file, an unknown target, or a `fact` column whose header gives no key is refused with 400.
- **FR-006**: A mapping MUST send a column to `company.name` or to `company.website`.
- **FR-007**: Columns that share a target are joined with a space, in the order of the file.
- **FR-008**: The stage, the source, and the owner of a company cannot be mapped. An imported company is a `LEAD` with the source `CSV` and no owner.
- **FR-009**: The suggestion is made from the header alone, without regard to capitals or punctuation. A header that is not recognized gets no suggestion.

Importing:

- **FR-010**: `POST /imports` stores the file and the mapping, creates the `import_batch` with its `row_count`, puts one message on the `RUNS` queue, and answers 202. It creates no company itself.
- **FR-011**: A duplicate is matched on the domain, against the companies that are not archived and against the earlier rows of the file.
- **FR-012**: Rows are stored 50 at a time, each 50 in one transaction. When the database refuses a transaction, its rows are stored one at a time, so that one bad row does not lose the others.
- **FR-013**: The counts of the `import_batch` are updated after every 50 rows.
- **FR-014**: `import_batch` holds only counts, so the list of duplicates, the list of errors, the status, and the message are kept in the bucket, as is the file with its mapping: `imports/<id>/result.json` and `imports/<id>/input.json`. An import with no result is `RUNNING`.
- **FR-015**: The queue handler MUST NOT raise. A failure is written to the Worker log (`carmen_import`) and the result is `FAILED`.
- **FR-016**: An error names the row and gives a reason that holds nothing from the database.

Undo:

- **FR-017**: Undo deletes the companies of the batch that are untouched: stage `LEAD`, not archived, never edited, and with no timeline entry, task, deal, agreement, document, AI run, or rubric answer.
- **FR-018**: Each deleted company is written to the Worker log first, as every delete is (`specs/018-change-log/spec.md`). The timeline goes with the company.
- **FR-019**: The `import_batch` and its stored file and result are kept, so the import can still be looked up.
- **FR-020**: A `MEMBER` may undo an import they made. Any other undo needs a role that may delete.

### Endpoints

| Method and path | Action | Does |
| --- | --- | --- |
| `POST /api/v1/imports/preview` | `WRITE` | Headers, suggested mapping, targets, row count, and the first 10 rows |
| `POST /api/v1/imports` | `WRITE` | Starts an import; 202 with the id |
| `GET /api/v1/imports/{id}` | `READ` | The batch with its counts, `status`, `message`, `duplicates`, and `errors` |
| `DELETE /api/v1/imports/{id}` | `WRITE`, then `may_delete` | Undo; answers `deleted` and `kept` |

### Not in this feature

- A list of past imports. An import is looked up by its id.
- Updating a company that is already there from a duplicate row.
- Matching a duplicate on anything but the domain, such as the name.
- A timeline entry on each imported company. The company's source and batch say where it came from.
- Removing the stored file of an import.
- Files in other forms, such as a spreadsheet workbook or a file with another separator.

### What is not tested

- A refused transaction, and a row the database refuses. Nothing made the database refuse a row in the tests.
- An import that is delivered a second time.
- A file in a character set other than UTF-8. The caller sends text, so the caller decodes the file.
- Undo by a `MEMBER`. The preview is called with the service token, which is an `ADMIN`.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: it previews a file, has a bad mapping refused, imports three rows, reads one company, one duplicate, and one error, and undoes the import.
- **SC-003**: An import of 2,000 rows finishes on the preview.

## Manual steps

None. The setting `import.max_rows` is migration `0010_import_max_rows` in `andexor-carmen-main`, applied to the preview and to production on 2026-11-24.
