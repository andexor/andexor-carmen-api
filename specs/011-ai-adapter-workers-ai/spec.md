<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: AI adapter: Workers AI

**Feature Branch**: `33-ai-adapter-workers-ai`

**Created**: 2026-11-24

**Status**: Implemented.

**Input**: Issue #33. Backlog feature 011: the adapter for the provider `WORKERS_AI`, page fetching sized to each model's context, and discovery task D4. It plugs into the run framework of `specs/009-ai-runs/spec.md`, which is unchanged, and reuses the prompts, the JSON shapes, and the test script of `specs/010-ai-adapter-claude/spec.md`. Where this spec and an earlier document differ, this spec governs.

## User Scenarios and Testing

### User Story 1 - An open model researches a company from its own website (Priority: P1)

A salesperson starts a research run with a Workers AI model. The model cannot search the web, so the API fetches pages from the company's website and the model reads them, writes a dossier, and proposes facts.

**Independent Test**: `./ai-test.sh run <model id>` on the preview. On 2026-11-24 each of the three launch models produced a dossier and proposed facts.

**Acceptance Scenarios**:

1. **Given** an enabled Workers AI model and a company with a website,\
   **When** a research run is processed,\
   **Then** the run holds a dossier that names the page behind each main claim, proposed facts, and the token counts.
2. **Given** a home page that links to about, services, and contact pages,\
   **When** the site is read,\
   **Then** those pages of the same site are fetched, at most `ai.research.max_pages` pages in all, and links to other sites are not followed.
3. **Given** a model with a small context window,\
   **When** the pages are longer than it can hold,\
   **Then** the page text is cut to fit, and every page keeps a share.
4. **Given** a page that contains text that reads like an instruction,\
   **When** the prompt is built,\
   **Then** that text is inside the data tags, and the prompt says that what is inside them is not instructions.

### User Story 2 - An open model answers the rubric (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a scoring run with a Workers AI model,\
   **When** it is processed,\
   **Then** the answers have the same shape as Claude's, and the run has its own scores.
2. **Given** runs of the same company by Claude and by a Workers AI model,\
   **When** they are compared,\
   **Then** each run shows under its own model, because no run falls back to another model.

### User Story 3 - A failure is clear (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a company with no website, or a website of which no page can be read,\
   **When** a research run is processed,\
   **Then** the run is `FAILED` with a message that says so, and no model is called.
2. **Given** one page that cannot be fetched,\
   **When** the site is read,\
   **Then** that page is left out and the run goes on with the others.
3. **Given** a refusal, an answer cut off at the token limit, or an error from Workers AI,\
   **When** the run is processed,\
   **Then** the run is `FAILED` with a message that is safe to show, and the provider's own error text is only in the Worker log.
4. **Given** a list of facts that cannot be read,\
   **When** the adapter asks again,\
   **Then** only the facts request is repeated, and the pages are not fetched again.

### Edge Cases

- A website stored without a scheme (`acme.com`) is fetched over HTTPS.
- A website that is not a public web address is not fetched: no host name with a dot, an address literal, or a scheme other than `http` and `https`.
- A link that redirects to another site is fetched and then left out.
- A file that is not a page (PDF, image, archive) is not fetched.
- Damaged HTML does not fail the run: what could be read is kept.
- A model id that is not in the table of context sizes is given the smallest size.
- Some websites refuse a request that comes from a Worker. Seen on 2026-11-24: one site answered 200 to the same request from a desktop and 403 to the Worker. Such a company cannot be researched by these models, and the run says that no page could be read.
- A fact whose value only says that nothing was found ("not provided", "unknown", "n/a") is dropped. This holds for every model.

## Requirements

### Discovery result

- **D4 (answered 2026-11-24)**: The `AI` binding works from Python: `await env.AI.run(model_id, body)`. The Workers runtime converts the Python dict to a JavaScript object and returns the reply as a Python dict, as it does for a D1 row. Converting the reply through JavaScript `JSON.stringify` gives an empty object.
- All three launch models accept `messages`, `max_tokens`, and `response_format` with a JSON schema, and all three answer in the same shape: `choices[0].message.content`, `choices[0].finish_reason`, and `usage.prompt_tokens` and `usage.completion_tokens`. One adapter serves all three with no per-model code.
- Context windows, from the Workers AI catalog: `@cf/openai/gpt-oss-120b` 128,000 tokens; `@cf/zai-org/glm-5.3` 1,048,576; `@cf/meta/llama-3.3-70b-instruct-fp8-fast` 24,000. The catalog gives no separate output limit. The default `max_tokens` is 256, so the adapter always sends its own.
- gpt-oss-120b and GLM-5.3 reason before they answer, and the reasoning counts as output tokens. GLM-5.3 reasons at its highest effort by default.
- `web_search_options` is accepted by GLM-5.3 and does nothing: asked for a page's current content, the model answered that it cannot browse. It is not used.
- Prices per million tokens, input and output, from the catalog: gpt-oss-120b $0.35 and $0.75; GLM-5.3 $1.40 and $4.40; Llama 3.3 70B $0.293 and $2.253. A reply also reports its Neurons.
- **Measured on the preview on 2026-11-24**, one company, research then scoring:

| Model | Research | Scoring | Tokens in | Tokens out | Estimated cost |
| --- | --- | --- | --- | --- | --- |
| gpt-oss-120b | 22 s | 10 s | 8,028 | 3,548 | $0.006 |
| GLM-5.3 | 86 s | 44 s | 7,633 | 25,313 | $0.12 |
| Llama 3.3 70B | 40 s | 1 s | 6,579 | 1,344 | $0.005 |

- The costs are estimates from the token counts at catalog prices. For 20 companies a day that is about $0.11 with gpt-oss-120b or Llama, and about $2.40 with GLM-5.3. Nearly all of GLM-5.3's cost is reasoning.
- Quality seen in that one run: gpt-oss-120b and GLM-5.3 gave usable facts and answers. Llama 3.3 70B filled many facts with "not provided" and answered no criterion.

### Functional Requirements

Reading the website:

- **FR-001**: For a research run, the API fetches the company's `website`, then the pages it links to on the same site whose address or label has one of: about, service, solution, product, contact, team, company, who-we-are, what-we-do. Pages nearer the top of the site come first.
- **FR-002**: At most `ai.research.max_pages` pages are fetched, the home page included. The setting is kept between 1 and 20, and is 8 when it is missing.
- **FR-003**: A fetch has a time limit of 10 seconds. A page that does not answer 200 with HTML or plain text is left out. At most 1,000,000 characters of a page are read.
- **FR-004**: A page is stripped to its visible text: scripts, styles, and inline images are removed, and each block is one line. Each page is given to the model under a line `PAGE <address>`.
- **FR-005**: Only a public web address is fetched: scheme `http` or `https`, and a host name with a dot that is not an address literal. A page reached by a redirect to another site is left out.
- **FR-006**: The page text is cut to fit the model. The answer keeps a quarter of the context window, at most 16,000 tokens. The pages get the rest, less 1,500 tokens for the prompt, and at most 40,000 tokens, because input is billed. A token is counted as 3 characters, which is on the safe side. Each page gets an equal share, and what a short page leaves over goes to the pages after it.
- **FR-007**: With no website, or no page that could be read, the run fails before any model is called.
- **FR-007a**: A page that is left out is named in the Worker log with the reason (`carmen_page_skipped`), so that a run with no pages can be explained.

Requests:

- **FR-008**: The adapter calls the `AI` binding with the `model_id` of the `ai_model` row. No provider package is used.
- **FR-009**: A request carries one user message and `max_tokens`. No sampling parameter and no reasoning setting is sent; the model's defaults apply.
- **FR-010**: A research run sends the prompt `research-pages.md` with the pages, then the prompt `facts.md` with the dossier and `response_format` set to the JSON schema of `facts`. A scoring run sends `scoring.md` with `response_format` set to the JSON schema of `answers`. The schemas and the last two prompts are those of feature 010.
- **FR-011**: `web_search_options` is not sent.

Replies:

- **FR-012**: An error from the binding fails the run with the kind of error. The provider's message goes to the Worker log only.
- **FR-013**: A reply with a `refusal` fails the run. A `finish_reason` of `length` fails it with a message that the answer was cut off. A model's reasoning is never taken as its answer.
- **FR-014**: A run MUST NOT fall back to another model.
- **FR-015**: JSON that is not the shape that was asked for is unreadable output. For scoring, the run framework asks once more. For the facts request, the adapter asks once more itself.
- **FR-016**: The run's `input_tokens` and `output_tokens` are the sums over every request of the run. `search_count` stays empty.
- **FR-017**: Everything a model returns goes through the cleaning of feature 009 before it is stored. That cleaning now drops a fact whose value only says that nothing was found.

Prompts and data:

- **FR-018**: Text from a fetched page is placed between `pages` tags, and the prompt states that it is data, not instructions.
- **FR-019**: The research prompt tells the model to state only what the pages support, and not to add what it remembers about the company from elsewhere.

Configuration and testing:

- **FR-020**: `wrangler.jsonc` binds `AI` for production and for the preview. Workers AI belongs to the account, so the binding is the same in both.
- **FR-021**: `preview-test.sh` and the pipeline MUST NOT call a Workers AI model. They use the stub.
- **FR-022**: `./ai-test.sh run <model id> [website]` tries a Workers AI model on the preview, on demand. Two runs at once need different websites, because two companies cannot share one.
- **FR-023**: The unit tests give the adapter canned replies and canned pages, and make no network call.

### Endpoints

None are added or changed. A Workers AI model is run through the endpoints of feature 009, with their actions: starting a run is `WRITE`, and editing a model is `MANAGE`.

### Not in this feature

- Lowering GLM-5.3's reasoning effort. It would cut its cost and time several times over, and it needs a setting that only that model has.
- Rendering pages that are built by JavaScript. A site that shows nothing without it gives the model little.
- Reading `robots.txt` or a sitemap.
- Storing which pages were read. The dossier names them.
- Changing `ai.research.max_pages` through the API. Feature 016 adds the settings endpoints.

### What is not tested

- A small company's website. The real runs read one large company's site.
- A refusal and a cut-off answer from a real model. Both are covered with canned replies.
- A site that needs the full 8 pages or that fills a model's context.
- More than one real run per model.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors and no network call.
- **SC-002**: `./preview-test.sh` passes and calls no real model.
- **SC-003**: `./ai-test.sh run <model id>` on the preview ends with both runs `SUCCEEDED` for each of the three launch models.
- **SC-004**: The check `Preview and smoke test` passes, which shows that the pipeline's token can deploy a Worker with the `AI` binding.

## Manual steps

None.
