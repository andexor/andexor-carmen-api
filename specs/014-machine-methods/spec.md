<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Machine methods

**Feature Branch**: `42-machine-methods`

**Created**: 2026-11-24

**Status**: Implemented, and live on the preview.

**Input**: Issue #42. Backlog feature 014: the two methods through which another Andexor service, not a person, puts data into Carmen. The first caller will be `andexor-net-website-contact-us`, a Worker; Andexor Grass Spider comes later. Decided 2026-11-24: the methods work the same way in the preview and in production, and a service token is neither an administrator nor tied to a person. This changes `specs/001-skeleton-and-pipeline/spec.md`, FR-005: a service token is no longer refused outside the preview. Where this spec and an earlier document differ, this spec governs.

## The design

```
   another Worker (Contact Us)                 a caller that is not a Worker
              |                                             |
              |  Service Binding                            |  HTTPS, through Cloudflare Access,
              |  env.CARMEN.create_lead({...})              |  with a service token
              v                                             v
   +-----------------------------------------------------------------------+
   |  andexor-carmen-api                                                   |
   |                                                                       |
   |  methods on the entry point            POST /api/v1/machine/leads     |
   |  create_lead, add_facts                POST /api/v1/machine/facts     |
   |              \                              /                         |
   |               +----> create_lead, add_facts +   written once          |
   +-----------------------------------------------------------------------+
```

The picture is the same for the preview and for production. Each environment's caller binds that environment's Worker.

## User Scenarios and Testing

### User Story 1 - A service sends a lead (Priority: P1)

Someone fills in the Contact Us form. The Contact Us service stores its own row and then tells Carmen, and the person appears in Carmen as a lead without anyone typing.

**Independent Test**: `./preview-test.sh` sends a lead with a website, a contact, a note, and a fact, and reads them back from the company.

**Acceptance Scenarios**:

1. **Given** a lead with a source and a name or a website,\
   **When** `create_lead` is called,\
   **Then** a company is created as a `LEAD` with that source and source reference, and the answer holds its id and `created` true.
2. **Given** a lead with a contact, a note, and facts,\
   **When** the company is created,\
   **Then** the contact is its primary contact, the note is a timeline entry of the kind `NOTE` linked to the contact, and the facts are stored.
3. **Given** a lead with a contact and no company name and no website,\
   **When** `create_lead` is called,\
   **Then** the company is named after the contact: the name, or else the email.

### User Story 2 - The same lead does not make a second company (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a company that is not archived and has the domain of the lead's website,\
   **When** `create_lead` is called,\
   **Then** no company is created, the answer holds that company's id and `created` false, and the contact, note, and facts are added to it.
2. **Given** no company with the domain, and a company with the same source and source reference,\
   **When** `create_lead` is called,\
   **Then** that company is matched in the same way.
3. **Given** a matched company that already has a contact with the same email, or with the same name when the lead gives no email,\
   **When** the lead is added,\
   **Then** that person is not added again.
4. **Given** a matched company that already has a primary contact,\
   **When** a new contact is added,\
   **Then** the new contact is not primary.

### User Story 3 - A service sends facts (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a company id, or the domain of a company that is not archived,\
   **When** `add_facts` is called with facts and a source,\
   **Then** the facts are stored with that source, and the answer holds how many were added.
2. **Given** an id or a domain that no company has,\
   **When** `add_facts` is called,\
   **Then** the answer is not found, and nothing is stored.

### User Story 4 - Only machines get in, and only here (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a service token, in the preview or in production,\
   **When** it calls a machine endpoint,\
   **Then** the call is allowed.
2. **Given** a service token that is not the preview's test token,\
   **When** it calls any other endpoint,\
   **Then** the answer is 403.
3. **Given** a person with the role `MEMBER` or `ADMIN`,\
   **When** they call a machine endpoint,\
   **Then** the call is allowed.
4. **Given** a person with the role `READER`,\
   **When** they call a machine endpoint,\
   **Then** the answer is 403.

### Edge Cases

- A lead with no source, or with neither a name, a website, nor a contact, is a bad request. So is a contact with neither a name nor an email.
- A source that is not a `LEAD_SOURCE` code is refused by the database, and the answer is a bad request.
- Facts of a lead take the lead's source when it is also an `ENRICHMENT_SOURCE` code (`CONTACT_US`, `GRASS_SPIDER`, `CSV`, `MANUAL`), and `API` otherwise.
- A match on the domain looks only at companies that are not archived, as everywhere. A match on the source reference prefers a company that is not archived and accepts an archived one.
- Two calls at the same moment with the same new domain: the second finds the company the first made, and adds to it.
- A note and facts are added on every call. A caller that sends the same lead twice gets the note and the facts twice, and the contact once.
- A fact whose key is a well-known yes-or-no or date key is stored in the form of that type, as for a fact typed by hand.

## Requirements

### Discovery result

- **Answered 2026-11-24, on the preview**: a method on the Python entry point class is callable over a Service Binding: `await env.CARMEN.create_lead({...})`. The argument arrives as a JavaScript object with `to_py()`, and a returned Python dict arrives at the caller as an object. It was measured with the preview Worker bound to itself for the test; the binding and the test endpoint were removed afterward.
- An exception raised in a method reaches the caller as an error that carries the whole Python traceback. A refusal is therefore returned, not raised.

### Functional Requirements

The methods:

- **FR-001**: `create_lead` takes one object: `source` (required, a `LEAD_SOURCE` code), and optional `name`, `website`, `source_ref`, `note`, `contact` (`name`, `title`, `email`, `phone`, `role`, `notes`), and `facts` (a list of `key`, `value`, and optional `source_detail`, `notes`, `observed_at`).
- **FR-002**: `create_lead` matches first on the domain of `website`, then on `source` with `source_ref`. With no match it creates the company.
- **FR-003**: `create_lead` writes everything of one call in one transaction, and answers `company_id` and `created`.
- **FR-004**: `add_facts` takes one object: `company_id` or `domain`, `source` (required, an `ENRICHMENT_SOURCE` code), and `facts` (a list with at least one fact). It answers `added`.
- **FR-005**: Both methods are written once, with no request, and both doors call them.

The Service Binding door:

- **FR-006**: `create_lead` and `add_facts` are methods on the Worker's default entry point. Each takes the one object and returns one object.
- **FR-007**: A method MUST NOT raise. Success is `ok` true with the answer's fields. A refusal is `ok` false with `error` (`BAD_REQUEST`, `NOT_FOUND`, or `INTERNAL`) and a `message` that is safe to show. The detail of a failure goes to the Worker log.
- **FR-008**: The door has no token and no role check. Only a Worker of the same Cloudflare account that binds this Worker can reach it.

The endpoint door:

- **FR-009**: `POST /api/v1/machine/leads` answers 201 when the company was created and 200 when it was matched. `POST /api/v1/machine/facts` answers 200. A refusal has the error shape of every endpoint: 400, 404, or 500.
- **FR-010**: Both endpoints declare the action `MACHINE`.

Who may call:

- **FR-011**: A verified Cloudflare Access token with no email and with a common name is a service token. Its role is `MACHINE`, in every environment. That role may perform the action `MACHINE` and nothing else. It is not a `USER_ROLE` code, it is never stored, and a service token never gets an `app_user` row.
- **FR-012**: Where the variable `SERVICE_TOKENS_ARE_ADMIN` is `"true"`, a service token acts as `ADMIN` instead. This is set under `env.preview` only, so that the smoke test can call every endpoint, and it MUST NOT be set for production.
- **FR-013**: Of the people, a `MEMBER` and an `ADMIN` may perform the action `MACHINE`: it creates and adds records, as `WRITE` does. A `READER` may not.

### Endpoints

| Method and path | Action | Does |
| --- | --- | --- |
| `POST /api/v1/machine/leads` | `MACHINE` | `create_lead` |
| `POST /api/v1/machine/facts` | `MACHINE` | `add_facts` |

### Not in this feature

- The change in `andexor-net-website-contact-us` that calls `create_lead`. It is a spec in that repo: a Service Binding named for this Worker in each environment, and one call after the form's own row is stored.
- A service token for production. Cloudflare Access admits none there today, so the endpoint door is closed in production until one is issued. See Manual steps.
- Telling one service token from another. Every service token has the same role.
- Leaving out a note or a fact that the company already has.
- Scoring, research, or a notification when a lead arrives. Feature 015 adds notifications.

### What is not tested

- The Service Binding door in the pipeline. No outside caller can reach it, so the smoke test calls the endpoint door, which runs the same code. The binding was proven once by hand; see Discovery result.
- A service token whose role is `MACHINE`, against a deployed Worker. The preview's token is an `ADMIN`, and production admits no token. The rule is covered by unit tests.
- Two calls at the same moment with the same new domain.
- A call from a Worker written in Python. The proof called from this Worker itself, which is Python.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: it creates a lead, matches it again by its domain and by its source reference, reads the contacts, the note, and the facts, adds facts by domain, and has a bad lead and an unknown company refused.
- **SC-003**: On the preview, both methods were called over a Service Binding and answered, and a refusal came back as an answer.

## Manual steps

None now. One later, recorded in `MANUAL-TASKS.md`: when a caller that is not a Worker needs production, issue a service token for it and add a Service Auth policy for that token to the production Access application.
