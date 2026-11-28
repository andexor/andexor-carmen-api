<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: A service id is in the form of a page name

**Feature Branch**: `82-service-id-form`

**Created**: 2026-11-28

**Status**: Implemented, and live on the preview.

**Input**: Issue #82. Finding 7 of the audit of 2026-11-27. Ed chose the fix on 2026-11-28: check the form of a given id. This spec changes `specs/004-services-rubric-and-scoring/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

The id of a service is made from its name, in the form of the page names on andexor.net: `seo-audit`. `POST /services` also took an id from the caller and stored it as given. The services of a deal and of an agreement are read from the database as one text with commas between the ids, so a service with the id `seo,audit` came back as two services, `audit` and `seo`, and neither exists. An id with a slash or a space could be stored but never reached again, because the id is part of the address of `PATCH /services/{service_id}`. An id cannot be changed after it is stored.

## User Scenarios and Testing

### User Story 1 - An id that would break a list or an address is refused (Priority: P2)

**Independent Test**: The unit tests give `records.new_service_id` ids in and out of the form. `./preview-test.sh` sends a service with a comma in its id and is refused.

**Acceptance Scenarios**:

1. **Given** a new service with the name "SEO Audit" and no id,\
   **When** it is created,\
   **Then** its id is `seo-audit`, as before.
2. **Given** a new service with the id `seo`,\
   **When** it is created,\
   **Then** its id is `seo`, as before.
3. **Given** a new service with the id `seo,audit`,\
   **When** it is sent,\
   **Then** it is refused with 400, and nothing is stored.
4. **Given** a new service whose id holds a slash, a space, a capital letter, or two hyphens in a row,\
   **When** it is sent,\
   **Then** it is refused with 400.

### Edge Cases

- A name written in a script other than Latin gives no id, and the service is refused with 400, as before. The manager gives an id in Latin letters. This matters when the team works in several languages; the id stays the page name, and the name is free.
- A stored service is not checked again. On 2026-11-28 both databases held 8 services, all in the form.

## Requirements

### Functional Requirements

- **FR-001**: `records.new_service_id(given, name)` answers the id of a new service: the given id, or the id that `slug` makes from the name.
- **FR-002**: A given id is accepted only when `slug` leaves it unchanged: lowercase letters and digits, joined by single hyphens, with no hyphen at either end. Any other id raises `BadBody`.
- **FR-003**: `POST /services` uses `records.new_service_id` and answers 400 for a refused id.
- **FR-004**: The lists of services of a deal and of an agreement stay as they are: no id can hold a comma.

### Endpoints

None are added, and no action changes. `POST /services` (`MANAGE`) refuses more bodies than before.

### Not in this feature

- A rule in the database. A migration could add one later; no stored id breaks the rule.
- Changing the id of a stored service.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: a service with a comma in its id is refused.
