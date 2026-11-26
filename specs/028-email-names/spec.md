<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: A name on the From and To lines of every email

**Feature Branch**: `60-email-names`

**Created**: 2026-11-26

**Status**: Implemented.

**Input**: Issue #60. Asked for on 2026-11-26: the From line and the To line of an email say the name, not the address alone. The To line reads `First Last <address>`, for a user and equally for the contact of a lead, a prospect, or a customer. The From line reads `Carmen <address>` in production and `Carmen PREV <address>` on the preview, so the sender shows which environment wrote. This spec changes `specs/015-scheduler-and-notifications/spec.md` (FR-011) and `specs/016-settings/spec.md` (the rule for `digest.recipient`). Where this spec and an earlier document differ, this spec governs. The subject line is `specs/029-email-subject-prefix/spec.md`.

## Why

A message from a bare address looks careless. And the preview can send real email on demand (`./email-test.sh`, a proposals job with `real=true`) from the same address as production, so until now nothing in the From line told the two apart.

## User Scenarios and Testing

### User Story 1 - The sender has a name that shows the environment (Priority: P1)

**Independent Test**: `./email-test.sh` sends the digest from the preview. In the inbox, the From line reads `Carmen PREV <address>`.

**Acceptance Scenarios**:

1. **Given** the production Worker,\
   **When** it sends an email,\
   **Then** the From line reads `Carmen <address>`.
2. **Given** the preview Worker,\
   **When** it sends a real email,\
   **Then** the From line reads `Carmen PREV <address>`.
3. **Given** the preview Worker with the stub provider,\
   **When** a message is made,\
   **Then** nothing is sent, and the Worker log shows the From line and the To line the message would have had.

### User Story 2 - The recipient is addressed by name (Priority: P1)

**Acceptance Scenarios**:

1. **Given** the setting `digest.recipient` holds `First Last <address>`,\
   **When** the digest, an alert, or a proposals email is sent,\
   **Then** the To line reads `First Last <address>`, and the stored message shows the same.
2. **Given** the setting holds an address with no name,\
   **When** an email is sent,\
   **Then** it goes to the bare address, and the Worker log says that the recipient has no name.
3. **Given** a person changes the setting,\
   **When** the value is `First Last <address>` or a bare address,\
   **Then** it is accepted.
4. **Given** a person changes the setting,\
   **When** the value holds no valid address, two addresses, or a name with a line break,\
   **Then** it is refused with a 400 that says what the setting must be.

### Edge Cases

- A name with a comma is written in quotes: `"Last, First" <address>`. The quotes are not part of the name.
- `<address>` alone is a bare address.
- Today the setting `digest.recipient` is the only recipient. Its name is part of its value, because a user's row has no name yet: Cloudflare Access gives none, and no endpoint sets `display_name`.
- The `send_email` binding takes a named side as an object with `email` and `name`, both required, and a bare address as text. So a side with no name is passed as text.

## Requirements

### Functional Requirements

- **FR-001**: A sender or a recipient is written `Name <address>`, or as a bare address when no name is known. `records.mailbox` reads the two parts; nothing else parses this form.
- **FR-002**: The variable `EMAIL_FROM` holds the sender in that form: `Carmen <address>` at the top level of `wrangler.jsonc` and `Carmen PREV <address>` under `env.preview`. The name on the preview MUST differ from the name in production.
- **FR-003**: The setting `digest.recipient` holds one recipient in that form. `records.setting_problem` accepts a name with one valid address or a bare address, and refuses anything else, including a name with `<`, `>`, a double quote, or a character that cannot be printed.
- **FR-004**: The `CLOUDFLARE` provider gives the binding each side through `notify.party`: an object with `email` and `name`, or the bare address. The `STUB` provider writes the From line and the To line to the Worker log.
- **FR-005**: A recipient with no name still gets the message. `notification(...)` writes `carmen_scheduler_no_recipient_name` to the Worker log, with the kind of the message and no address.
- **FR-006**: The `recipient` of a `notification` row is the To line as it was sent.
- **FR-007**: A message to any other recipient, such as the contact of a lead, a prospect, or a customer, MUST pass the recipient in the same form, with the name from that person's record. No such message exists yet; the feature that adds one follows this rule.

### Endpoints

None are added. `PATCH /api/v1/settings` (`MANAGE`) accepts the new form of `digest.recipient`.

### Not in this feature

- The subject line (`specs/029-email-subject-prefix/spec.md`).
- A name on a user's row, and an endpoint to set it.
- Email to contacts.

### What is not tested

- The smoke test and the pipeline send no real email, so they do not prove what an inbox shows. That is seen with `./email-test.sh`, on demand.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes.
- **SC-003**: A real digest from the preview shows `Carmen PREV <address>` in the From line and the recipient's name in the To line.
