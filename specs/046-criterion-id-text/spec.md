<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: An answer whose question id is not a text is dropped

**Feature Branch**: `106-criterion-id-text`

**Created**: 2026-11-28

**Status**: Implemented, and live on the preview.

**Input**: Issue #106. Finding 18 of the audit of 2026-11-27. Ed chose the fix on 2026-11-28: drop the answer. This spec adds to `specs/009-ai-runs/spec.md`. Where this spec and an earlier document differ, this spec governs.

## Why

Everything a scoring model returns goes through `ai.clean_answers`, which keeps the good answers and drops the bad ones. It looked up each answer's `criterion_id` as given. A list or an object there cannot be looked up: the function raised, and the whole run failed after the model was called and paid for, with its good answers lost. A newer or weaker model is the kind that wraps a value in a list.

## User Scenarios and Testing

### User Story 1 - One badly formed answer costs one answer (Priority: P3)

**Independent Test**: The unit tests give `ai.clean_answers` answers whose `criterion_id` is a list, an object, a number, and nothing, next to a good answer.

**Acceptance Scenarios**:

1. **Given** a model's output with 20 answers, one of which has the `criterion_id` `["has_website"]`,\
   **When** the output is cleaned,\
   **Then** 19 answers are kept, 1 is counted as dropped, and the run goes on.
2. **Given** an answer whose `criterion_id` is an object, a number, or missing,\
   **When** the output is cleaned,\
   **Then** it is dropped and counted, like an answer for a question that does not exist.
3. **Given** an answer whose `criterion_id` is the text of a question in use,\
   **When** the output is cleaned,\
   **Then** it is kept as before.

### Edge Cases

- `ai.clean_facts` needs no change: it turns a fact's key into text before it uses it.
- Jev's answers are built by code from its judgments, so their ids are always text.

## Requirements

### Functional Requirements

- **FR-001**: `ai.clean_answers` looks up an answer's `criterion_id` only when it is a text. Any other value drops the answer and adds one to the count of dropped answers. The function does not raise for any shape of an answer.
- **FR-002**: The run's log line `answers_dropped` counts such an answer, as for every dropped answer.

### Endpoints

None are added, and no action changes.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors. The new test fails on the code before this change with `unhashable type`.
- **SC-002**: `./preview-test.sh` passes as before.
