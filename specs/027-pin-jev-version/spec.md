<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Pin Jev to a numbered version

**Feature Branch**: `58-pin-jev-version`

**Created**: 2026-11-26

**Status**: Implemented. The numbered version is in the preview and the production database.

**Input**: Issue #58. Asked for on 2026-11-26: Jev is called by the latest numbered version, not by the name `jev-latest`. An upgrade of Jev is treated with care: when a new numbered version is available, its release notes are read, any manual work on data or logic is reported, and the upgrade is made as one coordinated change. This spec changes `specs/012-ai-adapter-typesafe/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

`jev-latest` is an alias that moves to each new release. TypeSafe says of it: "An alias moves when a new release ships, so its answers can change without any change to your code", and "If you have tuned confidence thresholds against one version, send that version's ID instead of the alias, and switch to the new version when you are ready" (`https://docs.typesafe.ai/models.md`, read on 2026-11-26). The thresholds in `src/ai.py` (`YES_AT`, `NO_AT`, `ANSWERABLE_AT`, `RATING_LEVELS`) and the wording of the questions were set against Jev 1.14.

## User Scenarios and Testing

### User Story 1 - Jev answers as one known version (Priority: P1)

**Independent Test**: The model's row in each database holds the numbered version. `./ai-test.sh run <Jev id> --research <model id>` shows the version on the run.

**Acceptance Scenarios**:

1. **Given** the model Jev,\
   **When** a scoring run calls it,\
   **Then** the request names `jev-1.14.0`, and the run stores the version that answered.
2. **Given** TypeSafe ships a new release,\
   **When** nothing is changed here,\
   **Then** Jev still answers as `jev-1.14.0`.

### User Story 2 - An upgrade is a coordinated change (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a newer numbered version is on TypeSafe's models page,\
   **When** it is noticed,\
   **Then** its release notes and the jaggedness page are read, and Ed is told what the release changes and whether data or logic here must change with it.
2. **Given** Ed decides to upgrade,\
   **When** the upgrade is made,\
   **Then** the new version, and every change to data and logic it needs, ship together: a migration in `andexor-carmen-main`, the code and the tests here, and a real run on the preview that is compared with a run of the old version.
3. **Given** a newer version exists and Ed has not decided,\
   **When** work goes on,\
   **Then** the version stays as it is.

### Edge Cases

- `jev-1.14.0` is the version `jev-latest` pointed to on 2026-11-26, so no answer changes with this feature.
- TypeSafe's list of models (`GET /v1/models`) shows the aliases only. A numbered version is accepted whether or not it is listed, so the latest number is read from the models page and the changelog.
- A numbered version can be withdrawn by the provider. The run then fails with the provider's error, as every failed call does; it is never answered by another version.
- A run stored before this feature keeps the version that answered it in `model_id_used`.

## Requirements

### Functional Requirements

- **FR-001**: The `model_id` of the Jev row in `ai_model` is a numbered version. It is `jev-1.14.0`, set by the migration `0017_pin_jev_version` in `andexor-carmen-main` (issue #57 there). No alias (`jev-latest`, `jev-preview`) is used, in data, code, or tests.
- **FR-002**: The version is data, not code: an upgrade changes it with a new migration, in both databases together.
- **FR-003**: Before an upgrade, these are read: the model changelog (`https://docs.typesafe.ai/changelog.md`), the models page (`https://docs.typesafe.ai/models.md`), and the jaggedness page (`https://docs.typesafe.ai/model-jaggedness.md`).
- **FR-004**: The report to Ed says, for the new version: what changed; whether the question wording, the thresholds, the request, or the reading of the reply in `src/ai.py` must change; whether stored answers should be scored again; and what the comparison run cost.
- **FR-005**: An upgrade is one coordinated change: the migration, the code and tests, a real scoring run on the preview with the new version, compared with one of the old version on the same research, and then production. A threshold is not fitted to a few cases.
- **FR-006**: Whenever work in this repo touches AI, or a session is asked about Jev, the pinned version is compared with the models page, and a newer version is reported. No upgrade is made without Ed's decision.

### Endpoints

None are added or changed.

### Release notes read for this version

Jev 1.14, released 2026-10-14 (`https://docs.typesafe.ai/changelog.md`, read on 2026-11-26): better multilingual support; the new Box question type, for images; images in a request; and long context of 262k tokens with a header. Carmen uses none of the new parts, and it already ran on this version, so no data or logic changes.

### Not in this feature

- An automatic check for a new version. The check is made by the person or the assistant who works here (FR-006).
- A guard in code that refuses an alias as a model id.
- Pinning the other providers' models. Their ids are already versions or the provider's fixed names.

### What is not tested

- A real run with the numbered version. It costs money, and no adapter, prompt, or threshold changed; the alias pointed to this version. The request is covered by the unit tests, which now use the numbered version.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: Both databases hold `jev-1.14.0` for Jev.
