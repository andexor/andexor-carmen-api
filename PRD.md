<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# PRD: Andexor Carmen API

Status: DRAFT 0.1
Date: 2026-11-15
Governed by: `andexor-carmen-main/PRD.md` (the master PRD). Section numbers below prefixed "master" refer to it. This document refines the master and may not contradict it.

## 1. Goal

Provide every back-end service Andexor Carmen needs: store and serve the common data model, compute Fit and Need scores, run AI research and scoring with a choice of models, take in leads from four sources, store agreement documents, and send reminders.

## 2. Context

- Name: `andexor-carmen-api`. Description: Andexor Carmen API.
- Language and framework: Python on Cloudflare Workers, FastAPI mounted through the Workers `asgi` module, managed with `uv`, run and deployed with `pywrangler`, as in `andexor-net-website-ping`.
- Route: `carmen.andexor.net/api/*` on zone `andexor.net`, declared in `wrangler.jsonc`. The preview Worker is `andexor-carmen-api-preview`, routed at `carmen-preview.andexor.net/api/*`. `workers_dev` and `preview_urls` are both false; nothing is served from `workers.dev`.
- The only caller with a browser is `andexor-carmen-ui`, on the same origin in production.
- This Worker is the only owner of the database and the bucket (master section 4).

### Bindings

| Binding | Kind | Production | Preview |
| --- | --- | --- | --- |
| `DB` | D1 | `andexor-carmen-api-prod` | `andexor-carmen-api-prev` |
| `DOCS` | R2 | `andexor-carmen-api-prod` | `andexor-carmen-api-prev` |
| `AI` | Workers AI | account | account |
| `CLAUDE_API_KEY` | Secrets Store (`carmen-claude-api-key`) | yes | not bound; preview uses the stub |
| `TYPESAFE_API_KEY` | Secrets Store (`carmen-typesafe-api-key`) | when available | not bound |
| `RUNS` | Queue (if discovery D2 chooses a queue) | `andexor-carmen-api-runs` | `andexor-carmen-api-runs-prev` |

Vars: `ALLOWED_ORIGINS`, `ACCESS_TEAM_DOMAIN`, `ACCESS_AUD`. Cron Trigger: hourly.

## 3. Data model

The schema is `andexor-carmen-main/db/carmen/`. This repo does not define tables. It carries a copy of the migration files, taken from the main repo at a pinned commit by `scripts/sync_schema.sh`, and applies them from the deploy workflow (section 10). JSON field names equal column names. Codes are passed through as stored.

## 4. Conventions for every endpoint

- **Base path:** `/api/v1`.
- **Authentication:** every request must carry a valid Cloudflare Access JSON Web Token (JWT) in `Cf-Access-Jwt-Assertion`, verified against the team's public keys, audience, and expiry. The user's email comes from the token. On first sight of an allowed email the API creates the `app_user` row. No valid token: 401. The one exception is `GET /api/v1/health`.
- **Roles:** enforced on every request, deny by default (constitution Principle VI; `specs/000-repo-setup/spec.md`, Change 5).
- **Required fields:** only those in master section 7. Unknown JSON keys are ignored. Missing optional keys are stored as NULL.
- **Validation:** types, SRT codes (the triggers are the last line of defense, not the first), and the required fields. Nothing else.
- **Lists:** `limit` (default 50, maximum 200) and `cursor`; a response carries `next_cursor` when there is more. Filters are query parameters named after columns. `q` searches name, domain, and contact name and email.
- **Success:** 200 with the resource, 201 with the created resource, 202 with a job, or 204.
- **Errors:** `{"ok": false, "error": "<CODE>", "request_id": "<id>"}` with 400, 401, 404, 409, 413, or 500. Detail goes to the log only, per constitution principle VIII.
- **Cross-Origin Resource Sharing (CORS):** echo the request's `Origin` when it is in `ALLOWED_ORIGINS`. Production needs none; preview and local development do.
- **Timeline:** every write that changes a stage, a score, or an agreement status also inserts an `activity` row. The UI never has to.
- **`updated_at`** is set by the API on every update.
- **Speed:** any operation measured above 1 second returns 202 and runs in the background.

## 5. Endpoints

### 5.1 System

| Method and path | Does |
| --- | --- |
| `GET /health` | 200 `{"ok": true}`. Liveness only; touches no dependency |
| `GET /me` | The signed-in user |
| `GET /srt` | All active SRT rows, for labels and pick lists |
| `GET /settings`, `PATCH /settings` | Read and change `setting` values |
| `GET /today` | One call for the Today view: tasks due and overdue, leads with no score, top leads by recommendation, deals with no open task, agreements inside their renewal window, runs still in progress |

### 5.2 Companies and contacts

| Method and path | Does |
| --- | --- |
| `GET /companies` | List; filter by `stage`, `source`, `recommendation`, `owner_id`, `archived`; sort by name, created, Fit, or Need |
| `POST /companies` | Create. Requires `name` or `website`. Derives `domain`. If the domain exists, returns 409 with the existing company's id |
| `GET /companies/{id}` | The company with its contacts, per-service Need, open deals, active agreements, and open tasks |
| `PATCH /companies/{id}` | Update any field, including `stage` (a `DISQUALIFIED` stage accepts `disqualified_reason`) |
| `DELETE /companies/{id}` | Delete with everything under it; 409 if it has agreements or documents |
| `POST /companies/{id}/archive`, `/unarchive` | Hide from, or return to, normal lists |
| `GET /companies/{id}/contacts`, `POST` | List; create (requires `name` or `email`) |
| `PATCH /contacts/{id}`, `DELETE /contacts/{id}` | Update, including `is_primary` (clears the previous primary); delete |

### 5.3 Enrichment facts

| Method and path | Does |
| --- | --- |
| `GET /companies/{id}/facts` | Facts, newest first; `latest=true` returns one per key |
| `POST /companies/{id}/facts` | Add one or many (`key`, `value`, optional `source_detail`); source `MANUAL` |
| `DELETE /facts/{id}` | Remove a fact |

### 5.4 Services and rubric

| Method and path | Does |
| --- | --- |
| `GET /services`, `POST /services`, `PATCH /services/{id}` | The catalog, including the `offered` switch. Changing `offered` recomputes Need for every company in the background |
| `GET /rubric` | All criteria, grouped Fit then by service |
| `POST /rubric`, `PATCH /rubric/{id}` | Create or change a criterion. Criteria are retired with `active = 0`, never deleted, so old runs stay readable. Changing weight, type, or configuration recomputes scores in the background |
| `GET /companies/{id}/answers` | The answers in effect, with each criterion |
| `PUT /companies/{id}/answers` | Set, change, or clear (value null) one or many answers; source `MANUAL`. Recomputes and returns the scores |

### 5.5 AI models and runs

| Method and path | Does |
| --- | --- |
| `GET /ai/models` | Models; filter by `can_research`, `can_score`, `active` |
| `POST /ai/models`, `PATCH /ai/models/{id}` | Add or change a model, including enabling and disabling it (`active`). The provider must have an adapter. A disabled model cannot start a run; its past runs remain |
| `POST /companies/{id}/runs` | Start runs. Body: `kind` (`RESEARCH` or `SCORING`), `ai_model_ids` (one or many), and for scoring an optional `input_run_id`. Creates one run per model and returns 202 with the run ids |
| `POST /companies/{id}/runs/research-and-score` | Convenience: one research run, then one scoring run per listed scoring model on its dossier |
| `GET /companies/{id}/runs` | Runs for the company, newest first, with model, status, scores, tokens, duration, rating |
| `GET /runs/{id}` | One run with its dossier and facts, or its answers and per-service scores |
| `PATCH /runs/{id}` | Set `user_rating` and `user_note` |
| `POST /runs/{id}/facts/decide` | Accept or reject proposed facts: a list of fact ids with `ACCEPTED` or `REJECTED`, or `all` |
| `POST /runs/{id}/apply` | Make a scoring run's answers the answers in effect; recompute and return the scores |
| `GET /companies/{id}/runs/compare?run_ids=` | Criterion by criterion: the answer in effect and each run's answer, points, probability, and rationale, with disagreements flagged |
| `GET /ai/models/stats` | Per model and kind: runs, failures, average rating, average duration, average tokens, and agreement rate with the answers in effect |

### 5.6 Deals

| Method and path | Does |
| --- | --- |
| `GET /deals` | List; filter by `stage`, `company_id`, `owner_id`; `open=true` excludes `WON` and `LOST` |
| `POST /deals` | Create. Requires `company_id`. Name defaults to "<company> deal". Moves a `LEAD` company to `PROSPECT` |
| `GET /deals/{id}`, `PATCH /deals/{id}`, `DELETE /deals/{id}` | Read; update, including `stage` and `service_ids`; delete. `WON` and `LOST` set `closed_at`; `LOST` accepts `lost_reason` |

### 5.7 Agreements and documents

| Method and path | Does |
| --- | --- |
| `GET /agreements` | List; filter by `company_id`, `type`, `status`, `engagement_model`; `renewing_within_days=` |
| `POST /agreements` | Create. Requires `company_id` and `type`. A parent must belong to the same company |
| `GET /agreements/{id}` | The agreement with its children, services, and documents |
| `PATCH /agreements/{id}` | Update. Status `ACTIVE` moves the company to `CUSTOMER`. When the last active agreement ends, the company moves to `FORMER` |
| `DELETE /agreements/{id}` | Delete a `DRAFT` only; 409 otherwise or if it has children |
| `POST /companies/{id}/documents` | Upload (multipart): file, optional `agreement_id`. Stores the object in R2, then the row. 413 above `document.max_size_bytes` |
| `GET /documents/{id}` | Metadata |
| `GET /documents/{id}/content` | The file, streamed, with its content type and a download file name |
| `DELETE /documents/{id}` | Delete the row, then the object |

### 5.8 Activities and tasks

| Method and path | Does |
| --- | --- |
| `GET /companies/{id}/activities` | The timeline, newest first; filter by `kind` |
| `POST /companies/{id}/activities` | Log a note, call, meeting, or email. Nothing but the company is required; kind defaults to `NOTE` |
| `PATCH /activities/{id}`, `DELETE /activities/{id}` | Hand-logged entries only; automatic entries are permanent |
| `GET /tasks` | List; filter by `status`, `company_id`, `due_before`, `owner_id` |
| `POST /tasks` | Create. Requires `title` |
| `PATCH /tasks/{id}`, `DELETE /tasks/{id}` | Update; `status: DONE` sets `completed_at` |

### 5.9 Import

| Method and path | Does |
| --- | --- |
| `POST /imports/preview` | Upload a CSV; returns the headers, a suggested mapping to company and contact fields, and the first rows |
| `POST /imports` | Upload the CSV with the chosen mapping. Unmapped columns can be kept as facts. Returns 202 with an `import_batch` id |
| `GET /imports/{id}` | Counts: rows, created, duplicates (matched on domain, listed), errors (listed with row numbers) |
| `DELETE /imports/{id}` | Undo: delete the companies this batch created that are still untouched `LEAD`s |

### 5.10 Machine methods

Exposed as Service Binding methods on the Worker entrypoint and, for callers that are not Workers, as `POST /api/v1/machine/leads` and `POST /api/v1/machine/facts` behind a Cloudflare Access service token.

- `create_lead(source, name?, website?, contact?, note?, source_ref?, facts?)` → `{company_id, created}`. Matches on domain, then on `(source, source_ref)`. A match adds the contact, note, and facts to the existing company.
- `add_facts(company_id | domain, facts[], source)` → `{added}`.

## 6. Scoring engine

A pure module with no input or output, so it can be tested without a Worker.

- `points(criterion, value)` implements the table in `andexor-carmen-main/db/README.md`.
- `score(criteria, answers, services)` implements master section 8.2 and returns Fit, Need per service, Need, confidence, and recommendation.
- It is called in three places: when answers in effect change (writes `company_service` and the company's scores, and a `SCORE_CHANGE` activity when Fit or Need moves by 5 or more or the recommendation changes); when a scoring run finishes (writes the run's own scores); and on recompute after a rubric or service change.
- Thresholds come from `setting` on every call.

## 7. AI runs

### 7.1 Provider interface

One adapter per `AI_PROVIDER` code, each with two methods:

- `research(company, pages?) -> {dossier, facts[], input_tokens, output_tokens}`
- `score(criteria, material) -> {answers[{criterion_id, value, probability?, rationale?}], input_tokens, output_tokens}`

| Adapter | Research | Scoring |
| --- | --- | --- |
| `CLAUDE` | Messages API over plain HTTPS with the web search tool. Asked for a dossier and a list of facts using the well-known keys | Messages API; asked for one answer per criterion in a fixed JSON shape, with a one-sentence rationale |
| `WORKERS_AI` | `AI` binding. The API first fetches up to `ai.research.max_pages` pages from the company's own site (home, about, services, contact), strips them to text, trims them to fit the model's context window, and passes them in | `AI` binding; same JSON shape as Claude |
| `TYPESAFE` | Not supported | One request carrying the material and one typed question per criterion: `YES_NO` as a yes-or-no question, `RATING` as a six-level scale, `CHOICE` as a choice. Probabilities are stored. `NUMBER` criteria are answered in code from accepted facts through `enrichment_key`, or left unknown. No rationale |
| `STUB` | Returns a fixed dossier and two facts | Returns deterministic answers derived from the criterion id |

No provider's Software Development Kit (SDK) is used; all calls are plain HTTP requests (master R1).

### 7.2 Run lifecycle

1. `POST .../runs` inserts one `ai_run` per model with status `QUEUED` and returns 202.
2. The work happens off the request (discovery D2 picks `waitUntil` or a Queue). The run moves to `RUNNING`, then `SUCCEEDED` or `FAILED` with `error`, `duration_ms`, and token counts.
3. Research writes `dossier` and `ai_run_fact` rows. Scoring writes `ai_run_answer` rows, runs the scoring engine, and writes the run's scores and `ai_run_service` rows.
4. A `SYSTEM` activity records that the run finished. Nothing on the company changes.
5. The UI polls `GET /runs/{id}`.

A model's output that cannot be parsed is retried once, then the run fails. An answer for a criterion that does not exist or a value outside the criterion's type is dropped and logged. Runs are started only by a user; there are no automatic or bulk runs in version 1.

### 7.3 Material for scoring

With `input_run_id`: that run's dossier and all of its facts, accepted or not. Without: the company's fields and its latest accepted fact per key. The material is identical for every model scoring the same input.

### 7.4 Prompts

Research and scoring prompts are files in `prompts/`, with named placeholders, so Ed can edit them. A prompt change is a normal pull request. Fetched page text is passed as data, marked as untrusted, and never as instructions.

## 8. Scheduler and notifications

The Cron Trigger fires hourly. Each firing:

- **Daily digest:** when the local hour in `digest.time_zone` equals `digest.hour_local` and no digest row exists for today, build the digest (tasks due today, tasks overdue, agreements in their renewal window, leads waiting for a score) and send it. Nothing is sent when every list is empty.
- **Renewal alerts:** for each `ACTIVE` agreement with an `end_date`, when today is `renewal_notice_days` (or the default) before it and no alert row exists for that agreement and date, send one alert and create an open task "Renewal: <title>" due on the notice date.
- **Expiry:** an `ACTIVE` agreement past its `end_date` that does not auto-renew becomes `EXPIRED`.

Sending goes through a provider interface `(channel, recipient, subject, body) -> provider_ref`. Version 1 ships the stub: it writes a `notification` row with status `STUBBED` and renders the message to the log (master Q5). Templates are files in `templates/email/`.

## 9. Scripts

| Script | Does |
| --- | --- |
| `scripts/create_env.py` | Creates the production D1 database and R2 bucket if missing and writes their ids into `wrangler.jsonc`. Idempotent |
| `scripts/sync_schema.sh` | Copies `db/carmen/*.sql` from `andexor-carmen-main` at the commit pinned in `schema.lock` |
| `scripts/migrate.py` | Applies unapplied migrations to the named environment, recording each in `schema_migration` |
| `preview-create.sh`, `preview-delete.sh`, `preview-test.sh` | As in the ping service, extended to the preview bucket and to activating the stub model in the preview database. Teardown refuses to touch production |
| `health.sh`, `deploy.sh`, `run.sh` | Health check, production deploy by hand, local run |

## 10. CI/CD

The pipeline in `andexor-net-website-ping/CICD.md`, adapted and recorded in this repo's `CICD.md`, with these differences:

- **Migrate before test.** The preview job runs `scripts/migrate.py` against the preview database before the smoke test. The production job runs it against production before deploying.
- **Smoke test** (preview only, through an Access service token): health check with retries; create a company from a website; read it back; set one answer and check that a Fit score comes back; start a stub research run and a stub scoring run and wait for both to succeed; apply the scoring run; upload a small document and download it; delete what it created. Any unexpected status fails the job.
- **Unit tests** for the scoring engine and the answer-to-points rules run before the preview deploy. This is a deliberate difference from the ping service, which has none: a wrong score is silent, and silent errors here cost real outreach decisions.
- **Token permissions:** Workers Scripts Edit, Workers Routes Edit on the `andexor.net` zone, D1 Edit (for migrations), Workers R2 Storage Edit, Workers AI Read, Secrets Store Edit, and Queues Edit if a queue is used. One token for this repo only.
- **Secrets in GitHub:** `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `ACCESS_CLIENT_ID`, `ACCESS_CLIENT_SECRET`, in both the Actions and Dependabot stores.
- Required check `Preview and smoke test`, the default-branch ruleset, Dependabot for `uv`, and Workers Builds disconnected, all as in the ping service.

### Manual tasks

These cannot be scripted and will be listed in `MANUAL-TASKS.md`, batched as in the ping service.

- **Workers AI:** nothing to switch on. It is included in the Workers plan and is reached through the `AI` binding declared in `wrangler.jsonc`. GLM-5.3 needs a paid billing method, which the Workers Paid plan satisfies. Usage above the daily free allocation is billed; set a billing notification in the Cloudflare dashboard.
- **Claude API key:** create a key in the Claude Console, add billing there, and store it in Cloudflare Secrets Store as `carmen-claude-api-key`.
- **TypeSafe key:** when available, store it as `carmen-typesafe-api-key`, then enable the Jev model in Settings.
- **Cloudflare Access:** one application for `carmen.andexor.net`, one for `carmen-preview.andexor.net`, and one service token for CI.
- **DNS:** the proxied `AAAA carmen` and `AAAA carmen-preview` records, each with content `100::`.
- **Cloudflare API token** for this repo, with the permissions above, added to the Actions and Dependabot secret stores.
- **GitHub:** the default-branch ruleset, after the workflow has run once.

## 11. Discovery tasks

| # | Question | Decides |
| --- | --- | --- |
| D1 | Cold start and request time for FastAPI with this many routes on Python Workers | Whether routes need trimming or lazy imports |
| D2 | Can a Python Worker finish a 60-second-plus AI call under `waitUntil`, or is a Queue consumer needed? | The run mechanism in 7.2 |
| D3 | Cloudflare Access on `carmen-preview.andexor.net`, with a service token from GitHub Actions | The preview gate (master R3) |
| D4 | Workers AI binding from Python with the three launch models; each model's context and output limits; whether the `web_search_options` parameter some models list is usable; what a day of normal use costs in Neurons | The `WORKERS_AI` adapter |
| D5 | The Claude Messages API web search tool: request shape, cost, and how to cap searches per run | The `CLAUDE` research adapter |
| D6 | TypeSafe Jev API: request shape for its three question types, limits, and access route | The `TYPESAFE` adapter and master Q3 |
| D7 | R2 upload and streamed download through a Python Worker at 25 MiB | The document size limit |
| D8 | Verifying an Access JWT in Pyodide without native cryptography packages | The authentication module |

## 12. Out of scope

- Everything out of scope in master section 1.
- Per-user data visibility, and audit logs beyond the timeline.
- Rate limiting and bot checks (Access stands in front).
- Automatic, scheduled, or bulk AI runs.
- Server-Sent Events (SSE); polling is enough for version 1.

## 13. Spec Kit feature backlog

Specify these in order with `/speckit-specify`. Each is a vertical slice that leaves the pipeline green.

| # | Feature | Covers |
| --- | --- | --- |
| 001 | Skeleton and pipeline | Worker, health, Access verification, role enforcement, `/me`, migrations runner, preview scripts, CI/CD, discovery D1, D3, D8 |
| 002 | Companies and contacts | 5.2, `/srt`, duplicate detection |
| 003 | Activities and tasks | 5.8, automatic timeline entries |
| 004 | Services, rubric, and scoring | 5.4, section 6, unit tests |
| 005 | Enrichment facts | 5.3 |
| 006 | Today | `GET /today` |
| 007 | Deals | 5.6, automatic stage changes |
| 008 | Agreements and documents | 5.7, R2, discovery D7 |
| 009 | AI runs: framework and stub | 5.5, 7.2, 7.3, compare, stats, discovery D2 |
| 010 | AI adapter: Claude | 7.1, discovery D5 |
| 011 | AI adapter: Workers AI | 7.1, page fetching sized to each model's context, discovery D4, the three launch models |
| 012 | AI adapter: TypeSafe (Jev) | 7.1, discovery D6 |
| 013 | CSV import | 5.9 |
| 014 | Machine methods | 5.10; then specs in the Contact Us and Grass Spider repos |
| 015 | Scheduler and notifications | Section 8 |
| 016 | Settings | `/settings`, model management |
