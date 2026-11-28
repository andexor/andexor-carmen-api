<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: A bad token is 401 and a number in other digits is 400

**Feature Branch**: `108-right-status`

**Created**: 2026-11-28

**Status**: Implemented, and live on the preview.

**Input**: Issue #108. Finding 19 of the audit of 2026-11-27. Ed chose the fix on 2026-11-28: refuse both cleanly. This spec adds to `specs/001-skeleton-and-pipeline/spec.md` and `specs/016-settings/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

A 500 says that Carmen failed. Two inputs got one though only the caller was wrong. A token whose header was valid JSON but not an object, such as `[]`, passed the reading step and failed on the next line. A setting whose number was written as a superscript two counted as digits but could not be read as a number. Digits of other scripts, such as Arabic-Indic or full-width digits, were accepted and stored as typed, so one number could be held in several spellings. Carmen will be used in several languages, and a keyboard set to one of them types exactly those digits.

## User Scenarios and Testing

### User Story 1 - A token that cannot be read is a bad token (Priority: P3)

**Independent Test**: The unit tests give `access.verify` a token whose header, and one whose payload, is a list, a number, a text, or nothing.

**Acceptance Scenarios**:

1. **Given** a token whose header is the JSON `[]`,\
   **When** the request is identified,\
   **Then** it is answered 401, and the log says "malformed token".
2. **Given** a token whose payload is JSON but not an object,\
   **When** the request is identified,\
   **Then** it is answered 401.

### User Story 2 - A number in a setting is written in the digits 0 to 9 (Priority: P3)

**Independent Test**: The unit tests give `records.setting_problem` a whole-number setting in four spellings. `./preview-test.sh` sends `digest.hour_local` as a superscript two and in Arabic-Indic digits.

**Acceptance Scenarios**:

1. **Given** the setting `digest.hour_local`,\
   **When** it is sent as a superscript two,\
   **Then** it is answered 400, and nothing is stored.
2. **Given** the setting `digest.hour_local`,\
   **When** it is sent as 10 in Arabic-Indic digits or in full-width digits,\
   **Then** it is answered 400, and nothing is stored.
3. **Given** the setting `digest.hour_local`,\
   **When** it is sent as `10`,\
   **Then** it is stored, as before.

### Edge Cases

- A screen in another language turns the digits a person typed into 0 to 9 before it sends them. The API does not rewrite a value.
- A setting that is put in the database by a migration is written in the digits 0 to 9. The code that reads a setting keeps its fallback for any other text.
- A token whose header is an object with a wrong algorithm, an unknown key, or a bad signature is answered 401, as before.

## Requirements

### Functional Requirements

- **FR-001**: `access.verify` raises `AccessError` ("malformed token") for a token whose header or payload is not a JSON object. `resolve_identity` answers 401 for it, as for every `AccessError`.
- **FR-002**: The rule for a whole number in `records.setting_problem` accepts only the ASCII digits 0 to 9. Every other text is a problem, and the rule does not raise for any text.
- **FR-003**: `PATCH /settings` answers 400 for such a value and stores none of the request's settings, as for every refused value.

### Endpoints

None are added, and no action changes. `PATCH /settings` is `MANAGE`.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors. The three new tests fail on the code before this change.
- **SC-002**: `./preview-test.sh` passes: both spellings of `digest.hour_local` are answered 400.
