<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Show why a company's website could not be read

**Feature Branch**: `35-page-fetch-reasons`

**Created**: 2026-11-24

**Status**: Implemented.

**Input**: Issue #35. Found while testing `specs/011-ai-adapter-workers-ai/spec.md`: a website answered 403 to the Worker, and the run said only that no page could be read. The reason was in the Worker log, which is kept for a limited time, and a site that refused some of its pages left no trace on the run. This spec adds to feature 011 and changes none of its limits. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - A refused website is explained on the run (Priority: P1)

A salesperson starts a research run with a model that reads the company's own website. The site refuses the request. The run fails, and its message says which page was asked for and what the site answered.

**Independent Test**: `./ai-test.sh run <a Workers AI model id> <a website that refuses the Worker>` on the preview. The run's `error` names the page and the status.

**Acceptance Scenarios**:

1. **Given** a website whose home page answers 403,\
   **When** a research run is processed,\
   **Then** the run is `FAILED`, and its message names the page and says that the site answered 403.
2. **Given** a company with no website, or a website that is not a public web address,\
   **When** a research run is processed,\
   **Then** the run is `FAILED`, and its message says that the company has no website address that can be fetched.
3. **Given** a website that does not answer in 10 seconds,\
   **When** a research run is processed,\
   **Then** the message says that there was no answer in 10 seconds.

### User Story 2 - A partly read website is noted on the timeline (Priority: P1)

Some pages are read and others are refused. The run succeeds on what it got, and the person can see that the model did not read everything.

**Acceptance Scenarios**:

1. **Given** a website of which 2 pages were read and 3 were not,\
   **When** the research run succeeds,\
   **Then** the company's timeline entry for the run says that 3 pages could not be read, names each, and gives each reason.
2. **Given** more than 5 pages that could not be read,\
   **When** the run succeeds,\
   **Then** the entry names 5 of them and says how many more there were.
3. **Given** a run in which every page was read, or a model that reads no pages,\
   **When** the run succeeds,\
   **Then** the entry is as before.

### Edge Cases

- A page that redirects to another site is left out with the reason "redirected to another site".
- A page with no readable text, such as one that is built entirely by JavaScript, is left out with the reason "no text could be read from the page".
- A file that is not a page is left out with the reason "not a web page".
- A failure message names at most 3 pages.

## Requirements

### Functional Requirements

- **FR-001**: Each page that is left out has a reason that is short and safe to show: the status the site answered, no answer in 10 seconds, not a web page, redirected to another site, redirected to an address that is not allowed, no text could be read from the page, or the kind of error.
- **FR-002**: The Worker log keeps the fuller detail of each page that is left out (`carmen_page_skipped`), as in feature 011. The text of an error is never shown to a user.
- **FR-003**: When no page could be read, the run fails with a message that names up to 3 pages with their reasons, and says that the model cannot search the web. The message is stored in the run's `error` and in the company's timeline entry for the failed run.
- **FR-004**: When some pages could be read, the run succeeds. The company's timeline entry for the run adds how many pages could not be read, and names up to 5 with their reasons.
- **FR-005**: No table and no endpoint changes. The reasons are stored in text that already exists: the run's `error` and the timeline entry.
- **FR-006**: An adapter returns the pages that could not be read as `skipped_pages`, a list of text. An adapter that reads no pages returns none.

### Endpoints

None are added or changed. The message is read through the endpoints of feature 009: a run's `error`, and the company's activities.

### Not in this feature

- A column on the run for the pages that were read or left out. The timeline entry holds them.
- Trying a refused site again, or with another User-Agent.
- Obeying `robots.txt`.

### What is not tested

- A real site that refuses only some of its pages. That case is covered with canned pages.
- A real time-out.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes.
- **SC-002**: `./preview-test.sh` passes and calls no real model.
- **SC-003**: On the preview, a research run of a website that refuses the Worker ends `FAILED` with a message that names the page and the status.

## Manual steps

None.
