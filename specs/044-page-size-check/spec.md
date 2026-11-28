<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: A page that says it is very large is left out

**Feature Branch**: `102-page-size-check`

**Created**: 2026-11-28

**Status**: Implemented, and live on the preview.

**Input**: Issue #102. Finding 16 of the audit of 2026-11-27. Ed chose the fix on 2026-11-28: check the `Content-Length` header only. This spec adds to `specs/011-ai-adapter-workers-ai/spec.md` and `specs/020-page-fetch-reasons/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

A model that cannot search reads the company's own website. Only the first 1,000,000 characters of a page are parsed, but the whole body was read into memory before it was cut. A Worker has 128 MB of memory, so one very large page could end the invocation, and with it the run, before any code could record a failure. Leads come from the website, so the address of such a page can come from a stranger.

## User Scenarios and Testing

### User Story 1 - An oversized page does not end the run (Priority: P3)

**Independent Test**: The unit tests give `pages.too_large` a length over the limit, at the limit, and lengths that are missing or not a number.

**Acceptance Scenarios**:

1. **Given** a page whose `Content-Length` header says 900,000,000,\
   **When** a research run fetches it,\
   **Then** its body is not read, the page is left out with the reason "the page is too large", and the run goes on with the other pages.
2. **Given** a page whose `Content-Length` header says 5,000,000 or less,\
   **When** a research run fetches it,\
   **Then** it is read and cut to 1,000,000 characters, as before.
3. **Given** a page with no `Content-Length` header, or one that is not a whole number,\
   **When** a research run fetches it,\
   **Then** it is read as before.

### Edge Cases

- The header counts the bytes as the site sends them. A compressed page is larger once it is unpacked, so the limit is well above the 1,000,000 characters that are parsed, and not a promise about memory.
- A site that sends no length, or a false one, is still read whole. That is the known limit of this fix; reading the body as a stream would close it.
- The home page is checked like any other page. When it is too large, the run fails with that reason, as for any home page that cannot be read.
- The uptime checker reads no body, so it is not changed.

## Requirements

### Functional Requirements

- **FR-001**: `pages.too_large(length)` answers true when the text is a whole number over `pages.MAX_BYTES` (5,000,000). It answers false for no value and for any text that is not a whole number in ASCII digits. It never raises.
- **FR-002**: `pages.fetch_page` checks the `Content-Length` header after the status and the content type, and before the body is read. A page that is too large is left out with the reason "the page is too large". The Worker log keeps the length that the site stated (`carmen_page_skipped`).
- **FR-003**: The reason is one more of the reasons of `specs/020-page-fetch-reasons/spec.md`: it goes into the run's failure message, or into the timeline entry when the run succeeds.

### Endpoints

None are added, and no action changes.

### What is not tested

- A real page over the limit against a deployed Worker. No public page of that size is known that states its length, and the smoke test reads no site outside `.example`. The check itself is covered by the unit tests.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes as before.
