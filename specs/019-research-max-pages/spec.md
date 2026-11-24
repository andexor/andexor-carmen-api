<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Read up to 16 pages for research

**Feature Branch**: `33-ai-adapter-workers-ai`

**Created**: 2026-11-24

**Status**: Implemented. The migration was applied to the preview and to production on 2026-11-24.

**Input**: Issue #33, asked for on 2026-11-24: "Increase ai.research.max_pages to 16", then "Change the constant in code back to 8 and create a database migration." It changes the stored value of one setting. No code of `specs/011-ai-adapter-workers-ai/spec.md` changes. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - A model reads more of a company's website (Priority: P2)

A model that cannot search the web reads up to 16 pages of the company's own site, the home page included, where it read up to 8.

**Independent Test**: After the migration, `SELECT value FROM setting WHERE key = 'ai.research.max_pages'` gives 16 in the preview and in production.

**Acceptance Scenarios**:

1. **Given** the stored setting `ai.research.max_pages` is 16,\
   **When** a research run reads a website,\
   **Then** at most 16 pages are fetched, the home page included.
2. **Given** a database with no such setting,\
   **When** a research run reads a website,\
   **Then** at most 8 pages are fetched, as in feature 011.
3. **Given** a model with a small context window and 16 pages,\
   **When** the page text is cut to fit,\
   **Then** every page keeps a share, and each share is smaller than it was with 8 pages.

### Edge Cases

- A site with fewer than 16 pages that are worth reading: all of them are read, as before.
- The most a stored setting can ask for stays 20.
- A database built from all migrations gets 8 from the first migration and 16 from the new one.

## Requirements

### Functional Requirements

- **FR-001**: The stored setting `ai.research.max_pages` is 16 in the preview and in production. The setting is the one place where the limit is changed.
- **FR-002**: The value is changed by the migration `0004_research_max_pages_16` in `andexor-carmen-main` (issue #22 there), which holds the schema, its seed, and the migration runner. This repo holds no schema. The migration is one statement: `UPDATE setting SET value = '16' WHERE key = 'ai.research.max_pages';`
- **FR-003**: The number in code stays 8. It is used only when the setting is missing, as in feature 011, FR-002.
- **FR-004**: The limits of feature 011 are unchanged: a stored value is kept between 1 and 20, the page text of one run is at most 40,000 tokens, and the text is cut to fit the model's context window.

### What changes for a run

- More pages share the same amount of text. With 16 pages, Llama 3.3 70B has room for about 1,000 tokens of each page, and the other two models about 2,500, where they had twice that with 8 pages.
- Pages are fetched one after another, each with a time limit of 10 seconds. 16 pages that all time out would take 160 seconds before the model is called. A run is processed by the queue, which ran 200 seconds in discovery D2.
- The cost of a run rises only as far as the page text grows, and that stays under the cap of 40,000 tokens.

### Endpoints

None are added or changed.

### Not in this feature

- Fetching pages at the same time, to shorten a run.
- Any change to code in this repo.

### What was tested

- On 2026-11-24 both databases read 16 for the setting and show schema version 4.
- One research run on the preview after the migration, with gpt-oss-120b on a site with many pages: it succeeded in 64 seconds with 17,696 input tokens, where a site with few pages took about 5,300, and no page was skipped.

### What is not tested

- That exactly 16 pages were read in that run. A run does not store which pages it read.

## Success Criteria

- **SC-001**: The setting reads 16 in the preview and in production.
- **SC-002**: A research run of a site with many pages reads up to 16 of them.

## Manual steps

None are left. The migration is applied to both databases. Its pull request in `andexor-carmen-main` is still to be merged, and its file must not change before then.
