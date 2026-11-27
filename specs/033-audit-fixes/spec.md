<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Audit fixes 1 to 5

**Feature Branch**: `78-audit-fixes-1-5`

**Created**: 2026-11-27

**Status**: Implemented, and live on the preview.

**Input**: Issue #78. An audit of the whole repo on 2026-11-27 gave 20 findings. Ed asked for the first five to be fixed; the others are decided one at a time. This spec changes `specs/009-ai-runs/spec.md`, `specs/011-ai-adapter-workers-ai/spec.md`, `specs/013-csv-import/spec.md`, `specs/014-machine-methods/spec.md`, `specs/015-scheduler-and-notifications/spec.md`, and `specs/026-uptime-checker/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

Each of the five let one record, one web page, or one moment of bad luck do damage far beyond itself: stop a daily job for every company, join two businesses into one company, leave a run stuck for good, or let a prospect's own page steer what a model says about it. Carmen takes leads from the website now, so text from strangers reaches all five.

## User Scenarios and Testing

### User Story 1 - Braces in a text do not stop an email or a run (Priority: P1)

**Independent Test**: The unit tests fill a prompt and an email with a value that holds `{{`. `./preview-test.sh` stores a lead whose name holds `{{`, and the scheduler run after it reports no failed step.

**Acceptance Scenarios**:

1. **Given** a lead named `Acme {{x`,\
   **When** the digest is made,\
   **Then** the digest is sent, and the name is shown as it was typed.
2. **Given** a web page that shows `Hello {{ user.name }}`,\
   **When** a research run reads it,\
   **Then** the run goes on, and the text reaches the model unchanged.
3. **Given** a value that holds the name of another placeholder, such as `{{pages}}`,\
   **When** the template is filled,\
   **Then** the value stays as it is: nothing is filled into it.
4. **Given** a template that names a placeholder with no value,\
   **When** it is filled,\
   **Then** it is an error, as before.

### User Story 2 - A website that is not an address is skipped (Priority: P1)

**Independent Test**: The unit tests give `pages.site_url` and `pages.pick_links` a text with an unbalanced bracket. `./preview-test.sh` stores a lead whose website is such a text, and the scheduler run after it reports no failed step.

**Acceptance Scenarios**:

1. **Given** a company whose website is `acme.com]`,\
   **When** the scheduler makes its uptime checks,\
   **Then** that company is skipped, and every other due company is checked.
2. **Given** a home page with the link `http://[YOUR_DOMAIN]/about`,\
   **When** a research run picks the pages to read,\
   **Then** the link is skipped, and the run goes on with the other pages.

### User Story 3 - Two businesses on one shared site are two companies (Priority: P1)

**Independent Test**: `./preview-test.sh` sends two leads whose websites are two pages on Facebook. Both answer `created` true, and the company has no domain.

**Acceptance Scenarios**:

1. **Given** a company whose website is `facebook.com/joes-plumbing`,\
   **When** a lead arrives with the website `facebook.com/anns-bakery`,\
   **Then** a second company is created, and nothing is added to the first.
2. **Given** a CSV file with two rows whose websites are pages on Instagram,\
   **When** it is imported,\
   **Then** both rows become companies, and neither is reported as a duplicate of the other.
3. **Given** a site is added to the setting `company.shared_hosts`,\
   **When** the next company on that site is stored,\
   **Then** it gets no domain, and no code was changed.
4. **Given** a company on a shared site,\
   **When** it is read,\
   **Then** its website is as given, and its domain is empty.

### User Story 4 - A run is never left running, and a finished run stays finished (Priority: P1)

**Independent Test**: The unit tests run the handler with a database that refuses to write, and start a waiting run with a queue that refuses the message.

**Acceptance Scenarios**:

1. **Given** a run fails and the failure cannot be written to the database,\
   **When** the queue handler finishes,\
   **Then** the message is given back to the queue, and on the next delivery the run is marked as interrupted.
2. **Given** a research run whose result is stored,\
   **When** a scoring run that waited for it cannot be started,\
   **Then** the research run stays `SUCCEEDED`, and only that scoring run is `FAILED`, with the message "The run could not be started".

### User Story 5 - Text from outside cannot leave its tags (Priority: P1)

**Independent Test**: The unit tests fill a prompt with a value that holds a closing tag, in several spellings.

**Acceptance Scenarios**:

1. **Given** a web page whose text holds `</pages>` followed by instructions,\
   **When** the prompt is made,\
   **Then** the tag is gone from the text, and the instructions are still between the prompt's own `pages` tags, where the model is told they are data.
2. **Given** a text that would leave a tag behind when one is taken out, such as `</pa</pages>ges>`,\
   **When** the prompt is made,\
   **Then** no tag is left.

### Edge Cases

- A host name in `company.shared_hosts` covers its subdomains: `facebook.com` covers `m.facebook.com`, and `google.com` covers `sites.google.com`. It does not cover a name that only ends in the same letters: `fox.com` is not `x.com`.
- A company on a shared site is found for a lead by its source and source reference, as a company with no website is. A lead from the same Facebook page with another reference makes a second company; a person merges them.
- A site that gives each business its own host name (`acme.wixsite.com`) is not a shared site: the host already tells businesses apart.
- A website that cannot be read as an address is stored as given, as before. The company has no domain, gets no uptime checks, and a research run of it says the website is not a public web address.
- The run handler gives a message back to the queue once: the queue delivers a message twice at most. After three failed writes in a row, a scoring run can still stay `QUEUED`.
- Taking the tags out is not the only defense: the answers of a model still go through `clean_facts` and `clean_answers`.

## Requirements

### Functional Requirements

Templates:

- **FR-001**: `records.fill` replaces each `{{name}}` in a template with its value in one pass over the template. A value is never read again. A name with no value is an error. A lone `{{` in a template that is not a placeholder is left as it is.
- **FR-002**: `ai.render` and `notify.render` fill their templates with `records.fill`. Neither looks for braces after filling.

Addresses:

- **FR-003**: `pages.site_url` answers none for a text that cannot be read as an address. `pages.pick_links` skips such a link. Neither raises.

Shared sites:

- **FR-004**: `records.derive_domain(website, shared)` answers none when the website's host is one of the shared hosts or a subdomain of one. `shared` is host names separated by commas.
- **FR-005**: The shared hosts are the setting `company.shared_hosts`, read on every use by `shared_hosts(database)`. The setting is created by the migration `0020_shared_hosts_setting` in `andexor-carmen-main` (issue #67 there). The list in `records.SHARED_HOSTS` is the fallback when the setting is missing or cannot be read; a write never fails because of this setting.
- **FR-006**: The setting is used wherever a company gets its domain: `POST /companies`, `PATCH /companies/{company_id}`, `create_lead`, and the CSV import.
- **FR-007**: `PATCH /settings` accepts for `company.shared_hosts` only host names separated by commas, and refuses a scheme, a path, or an empty item.

Runs:

- **FR-008**: `process_run` answers false when a run failed and the failure could not be recorded, and true otherwise. It still never raises.
- **FR-009**: The queue handler calls `message.retry()` for a run whose handler answered false, and `message.ack()` otherwise.
- **FR-010**: A run whose result is stored is never changed to `FAILED`. `start_waiting` queues the scoring runs that waited for a research run; one that cannot be queued is set to `FAILED` by its own id.

Prompts:

- **FR-011**: `ai.render` takes every opening and closing tag that the prompt file itself uses out of every value, in any letter case and with spaces inside the brackets, and again until none is left. The tag names are read from the prompt file, so a new prompt or a new tag needs no list in code.

### Endpoints

None are added, and no action changes. `GET /settings` and `PATCH /settings` (`MANAGE`) list and accept the new setting.

### Not in this feature

- The other 15 findings of the audit. Ed decides them one at a time.
- Joining two companies that are the same business. A person does that.
- A list of shared sites that is complete. It holds the common ones, and Ed adds to it in the settings.

### What is not tested

- The queue's `message.retry()` against a deployed Worker. A failed database write cannot be produced on the preview; the handler's answer is covered by a unit test.
- A real model reading a page that tries to leave its tags. It costs money, and no adapter or model changed; the prompt text is covered by a unit test.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: two leads on one shared site are two companies, and after a lead with braces in its name and a website that is not an address, both scheduler runs report no failed step.
- **SC-003**: Both databases hold the setting `company.shared_hosts`.
