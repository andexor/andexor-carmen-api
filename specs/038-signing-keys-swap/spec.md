<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: The signing keys are swapped in one step

**Feature Branch**: `90-signing-keys-swap`

**Created**: 2026-11-28

**Status**: Implemented, and live on the preview.

**Input**: Issue #90. Finding 11 of the audit of 2026-11-27. Ed chose the fix on 2026-11-28: build the new list aside and swap it in. This spec changes `specs/001-skeleton-and-pipeline/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

Carmen checks the signature of every Cloudflare Access token with Access's public keys, which it fetches and keeps in memory. When a token names a key that is not kept (a cold start, or a rotation of the keys), the list is fetched again. The kept list was emptied first and filled one key at a time, with a pause at each key, and other requests run during a pause. Two requests that fetched together could empty the list under each other, and one of them then did not find the key that signed its own token: a signed-in person was refused. A page of the UI sends several requests at once, and the first page after a quiet time meets a cold start.

## User Scenarios and Testing

### User Story 1 - Requests that arrive together are all accepted (Priority: P2)

**Independent Test**: A unit test runs five requests together against a key source whose answers arrive at different moments. It fails with "unknown signing key" on the code before this feature.

**Acceptance Scenarios**:

1. **Given** no keys are kept,\
   **When** several requests of signed-in people arrive at the same moment,\
   **Then** each one finds the key that signed its token, and none is refused.
2. **Given** keys are kept and a request names a new key,\
   **When** another request runs while the list is fetched,\
   **Then** it finds its key in the old list, and is accepted.
3. **Given** a token that names a key that Access does not publish,\
   **When** it is checked,\
   **Then** it is refused, as before.

### Edge Cases

- Two requests that fetch the list at the same moment each make their own fetch, as before. That costs one extra call to Access on a cold start and changes no answer.
- A fetch that fails leaves the kept list as it was.

## Requirements

### Functional Requirements

- **FR-001**: `access._signing_key` imports the fetched keys into a new list. When every key is imported, the kept list is emptied and filled from the new list with no pause between the two.
- **FR-002**: Nothing else changes: when the list is fetched, what is refused, and how long the keys are kept.

### Endpoints

None are added, and no action changes.

### Not in this feature

- Sharing one fetch between requests that arrive together.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes.
