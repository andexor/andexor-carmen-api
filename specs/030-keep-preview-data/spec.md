<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Tests delete nothing on the preview

**Feature Branch**: `64-keep-preview-data`

**Created**: 2026-11-26

**Status**: Implemented.

**Input**: Issue #64. Asked for on 2026-11-26: test data on the preview is left there, always, so it can be reviewed. "No deletes at all in CI/CD pipelines. Period." This spec changes the smoke test of every earlier feature spec, which cleaned up after itself. Where this spec and an earlier document differ, this spec governs.

## Why

The records a test makes are the evidence of what it did. A test that removes them leaves nothing to look at. And cleanup made two runs depend on each other: on 2026-11-26 a pipeline run failed when a second run on the same preview removed a document under it.

## User Scenarios and Testing

### User Story 1 - Everything a test made is still there (Priority: P1)

**Independent Test**: Run `./preview-test.sh` twice. Both pass, and the companies, agreements, documents, facts, deals, runs, imports, and notifications of both runs are on the preview.

**Acceptance Scenarios**:

1. **Given** the preview,\
   **When** `./preview-test.sh` runs, by hand or in the pipeline,\
   **Then** it makes no call that deletes a record.
2. **Given** the data of earlier runs on the preview,\
   **When** the smoke test runs again,\
   **Then** it passes, because each run has a tag of its own in its names and websites.
3. **Given** a delete that the API must refuse,\
   **When** the smoke test calls it,\
   **Then** the refusal (403, 404, or 409) is checked, and nothing is removed.
4. **Given** `./ai-test.sh run` with a website,\
   **When** the run ends,\
   **Then** the test company stays, and a later run with the same website uses that company again.

### Edge Cases

- Two companies cannot share a website. The smoke test's first company was `smoke-test.invalid` on every run and was deleted at the end; it is now `smoke-<tag>.invalid`.
- An overdue task of every run would fill the Today view's list. The smoke test marks its overdue task done after the check. That is an edit, not a delete.
- A new uptime measurement replaces a company's earlier checks. The smoke test starts one on a new company that has no checks, so nothing is replaced.
- Agreements stay in the status the test left them in: one expired, one completed, one active after its renewal, one draft.
- The stub model is switched off at the end and a changed setting is put back, as before. Those are edits that keep the preview like production.
- The preview grows by about seven companies with each run. `preview-delete.sh` and `preview-create.sh` start it over when a person decides to.

## Requirements

### Functional Requirements

- **FR-001**: `preview-test.sh` MUST NOT make a call that deletes a record on the preview: no cleanup of an earlier run, no cleanup at the end, and no check of a delete that succeeds.
- **FR-002**: A delete that must be refused is still called and its refusal checked.
- **FR-003**: Every name and website that must be unique carries a tag of the run.
- **FR-004**: `ai-test.sh run` MUST NOT delete its test company. It looks for a company with the website first and makes one only when there is none.
- **FR-005**: The same holds for every test a person or an assistant runs on the preview by hand, and for every script added later.
- **FR-006**: A delete endpoint is tested by the unit tests and by hand on a local run (`run.sh`), never on the preview by a script.

### Endpoints

None are added or changed.

### Not in this feature

- A change to any delete endpoint. They work as before.
- A limit on how much test data the preview holds.

### What is not tested

- These successful deletes are no longer exercised on a deployed Worker: a fact, a deal, a document with its object in the bucket, a draft agreement, a company with its records, and the undo of an import. Their refusals still are.

## Success Criteria

- **SC-001**: `preview-test.sh` contains no `DELETE` that expects a 2xx answer.
- **SC-002**: `./preview-test.sh` passes twice in a row on the preview.
