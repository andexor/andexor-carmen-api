<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: AI adapter: Claude

**Feature Branch**: `31-ai-adapter-claude`

**Created**: 2026-11-23

**Status**: Implemented.

**Input**: Issue #31. Backlog feature 010: the adapter for the provider `CLAUDE`, and discovery task D5. It plugs into the run framework of `specs/009-ai-runs/spec.md`, which is unchanged. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - Claude researches a company (Priority: P1)

A salesperson starts a research run with a Claude model. Claude searches the web, writes a dossier with its sources, and proposes facts.

**Independent Test**: `./ai-test.sh run <model id>` on the preview. On 2026-11-23 it produced a dossier with source URLs and 8 proposed facts.

**Acceptance Scenarios**:

1. **Given** an enabled Claude model that can research,\
   **When** a research run is processed,\
   **Then** the run holds a dossier, proposed facts with source URLs, the token counts, and the number of searches.
2. **Given** a cap of 5 searches,\
   **When** the run is processed,\
   **Then** no more than 5 searches are made.
3. **Given** a company record that contains text that reads like an instruction,\
   **When** the prompt is built,\
   **Then** that text is inside the data tags, and the prompt says that what is inside them is not instructions.

### User Story 2 - Claude answers the rubric (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a scoring run with a Claude model,\
   **When** it is processed,\
   **Then** each answer has a value of its criterion's type, a probability, and a one-sentence rationale, and the run has its own scores.
2. **Given** material that does not support an answer,\
   **When** Claude answers,\
   **Then** that criterion is left out and counts as unknown.

### User Story 3 - A failure is clear and costs nothing more (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a refusal, an answer cut off at the token limit, or an error from the API,\
   **When** the run is processed,\
   **Then** the run is `FAILED` with a message that is safe to show, and the provider's own error text is only in the Worker log.
2. **Given** a research run that fails,\
   **When** scoring runs were waiting for it,\
   **Then** they are failed with it, and the company's timeline records the failure.
3. **Given** a list of facts that cannot be read,\
   **When** the adapter asks again,\
   **Then** only the facts request is repeated, not the search.

### User Story 4 - Try it on the preview without paying on every change (Priority: P1)

A real model is tried on the preview before production, when someone chooses to. The pipeline never calls one.

**Acceptance Scenarios**:

1. **Given** a pull request,\
   **When** the pipeline's smoke test runs,\
   **Then** it uses only the stub and makes no paid call.
2. **Given** a developer who wants to see a real model work,\
   **When** they run `./ai-test.sh run <model id>`,\
   **Then** the model researches and scores a test company on the preview, the script prints the results and what was used, and deletes the test company.
3. **Given** a model that should not be run for now,\
   **When** `./ai-test.sh disable <model id>` is run,\
   **Then** nobody can start a run with it on the preview until it is enabled again.

### Edge Cases

- The API pauses a long search turn. The adapter sends the paused answer back unchanged, at most 3 times, then fails the run.
- The dossier is the text after the last tool block. Text before a search is only a note that the model is about to search.
- Cached input tokens are counted as input tokens.
- An empty dossier fails the run.
- A model id that the API does not know fails the run with the API's status.
- No key in Secrets Store, or no binding, fails the run with a message that says so.

## Requirements

### Discovery result

- **D5 (answered 2026-11-23)**: The web search tool is one entry in `tools`: `{"type": "web_search_20260209", "name": "web_search", "max_uses": N}`. `max_uses` caps the searches of one turn, and searches made before a pause count toward it. A search costs $10 per 1,000, so $0.01 each, on top of tokens, and the results are billed as input tokens. The number of searches comes back in `usage.server_tool_use.web_search_requests`. Web search is on for an organization unless an administrator turned it off in the Claude Console.
- Web search always returns citations, and the API refuses citations together with a fixed JSON output shape. Research is therefore two requests: one with web search, which writes the dossier, and one with no tools and a fixed JSON shape, which lists the dossier's facts.
- **Measured on the preview on 2026-11-23**, one company, model `claude-sonnet-5-5`: research took 47 seconds, 5 searches, 54,134 input tokens, and 4,005 output tokens; scoring took 6 seconds, 3,938 input tokens, and 569 output tokens. At $2 and $10 per million tokens that is about $0.20 for research and $0.01 for scoring. Most of the research cost is search results read as input.
- Research at 47 seconds is longer than the 30 seconds that work after a response is allowed, which confirms the queue of feature 009.

### Functional Requirements

Requests:

- **FR-001**: The adapter calls `POST https://api.anthropic.com/v1/messages` over plain HTTPS with the headers `x-api-key`, `anthropic-version: 2026-10-21`, and `content-type: application/json`. No provider package is used.
- **FR-002**: The model is the `model_id` of the `ai_model` row. Nothing about a particular Claude model is fixed in code, so a model is changed by editing its row.
- **FR-003**: `max_tokens` is 16000. No sampling parameter and no thinking setting is sent; the model's defaults apply.
- **FR-004**: A research run sends the research prompt with the web search tool and `max_uses`. `max_uses` is the setting `ai.research.max_searches` when it exists, kept between 1 and 20, and 5 otherwise.
- **FR-005**: A research run then sends the facts prompt, with the dossier, with no tools, and with `output_config.format` set to a JSON schema of `facts`: `key`, `value`, `source_url`, `notes`.
- **FR-006**: A scoring run sends the scoring prompt, with the criteria and the material, with no tools, and with `output_config.format` set to a JSON schema of `answers`: `criterion_id`, `value`, `probability`, `rationale`.
- **FR-007**: When a reply's `stop_reason` is `pause_turn`, the adapter sends the request again with the paused answer appended unchanged, and no other message. After 3 pauses the run fails.

Replies:

- **FR-008**: A status other than 200 fails the run with the status and the API's error type. The API's error message goes to the Worker log only.
- **FR-009**: `stop_reason` `refusal` fails the run with "Claude declined the request". `max_tokens` fails it with a message that the answer was cut off.
- **FR-010**: The adapter does not ask the API to fall back to another model after a refusal. Runs are compared per model, so an answer from a different model under this model's name would falsify the comparison. Decided 2026-11-23.
- **FR-011**: JSON that is not the shape that was asked for is unreadable output. For scoring, the run framework asks once more. For the facts request, the adapter asks once more itself, so that the search is not repeated.
- **FR-012**: The run's `input_tokens`, `output_tokens`, and `search_count` are the sums over every request of the run.
- **FR-013**: Everything Claude returns goes through the cleaning of feature 009 before it is stored.

Prompts:

- **FR-014**: The prompts are the files `src/prompts/research.md`, `facts.md`, and `scoring.md`. A placeholder is `{{name}}`. A placeholder with no value fails the run. The files are packaged with the Worker by a `rules` entry in `wrangler.jsonc`. A prompt change is a normal pull request.
- **FR-015**: Text from outside the API, which is the company record, the dossier, and the material, is placed between tags, and the prompt states that what is between them is data, not instructions, and that the same holds for web pages.
- **FR-016**: The company is given to a model as name, website, industry, NAICS code, employee count, city, region, country, and summary. Contact details and internal notes are not sent.

The key:

- **FR-017**: The API key is the Secrets Store secret `carmen-claude-api-key`, bound as `CLAUDE_API_KEY`. It is read when a request is sent, and is never logged, stored, or returned.
- **FR-018**: The key is bound for production and for the preview. Decided 2026-11-23: a real model must be testable on the preview before it reaches production. This replaces the earlier rule that the preview has no key.

Testing without paying on every change:

- **FR-019**: `preview-test.sh` and the pipeline MUST NOT call a paid model. They use the stub. A unit test fails if the smoke test names another provider.
- **FR-020**: `ai-test.sh` works on the preview only. `models` lists the models with whether each is enabled and runnable. `enable` and `disable` switch a model. `run <model id> [website] [--keep]` researches a test company and scores the dossier with that model, prints the dossier, the facts, the answers, and what each run used, and deletes the test company unless `--keep` is given.
- **FR-021**: The unit tests give the adapter canned replies and make no network call. They cover both request shapes, that every object in a schema is closed, the prompts and their data tags, the text after the last tool block, the pause, the refusal, the cut-off, an API error whose message must not reach the run, the usage sums, and the single repeat of the facts request.

### Not in this feature

- Fetching a company's own pages. Claude searches for itself.
- A choice of web search options beyond the cap: allowed or blocked domains, and a location.
- Streaming the answer.
- Prompt caching. Each run is one company, so little would be reused.
- Changing the search cap through the API. Feature 016 adds the settings endpoints.

### What is not tested

- A refusal and a paused turn against the real API. Both are covered with canned replies.
- More than one real run. One research-and-score was run on the preview.
- The pipeline's token deploying a Worker bound to Secrets Store. This feature's pull request is the first such deploy.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors and no network call.
- **SC-002**: `./preview-test.sh` passes and makes no paid call.
- **SC-003**: `./ai-test.sh run <a Claude model id>` on the preview ends with both runs `SUCCEEDED`.
- **SC-004**: The check `Preview and smoke test` passes.

## Manual steps

Listed in `MANUAL-TASKS.md`: check that the pipeline's API token has the Secrets Store permission.
