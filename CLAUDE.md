# andexor-carmen-api

Read, in this order, before doing anything here:

1. `andexor-carmen-main/CLAUDE.md`: the standing conventions for the three Carmen repos.
2. `andexor-carmen-main/PRD.md`: the master Product Requirements Document (PRD).
3. `andexor-carmen-main/db/README.md`: the common data model. Use its names unchanged.
4. `PRD.md` in this repo: what this app must do. It refines the master and may not contradict it.
5. `specs/` in this repo, starting with `specs/000-repo-setup/spec.md`: what changed since the PRDs were frozen. Where a spec and a PRD differ, the spec governs. Also read `andexor-carmen-main/specs/001-review-open-items/decisions.md`.
6. `.specify/memory/constitution.md`: copied from `andexor-carmen-main`. Do not edit it here.

Work follows Spec Kit: specification, plan, tasks, implement. The feature backlog is the last section of `PRD.md`; specify features in that order. Spec folders `001` to `016` carry the backlog's numbers.

A change that conflicts with the master PRD or the data model starts with a pull request in `andexor-carmen-main`.

## Working in this repo

- Python with `uv`; Wrangler only through `uv run pywrangler`; other Cloudflare resources through the `cf` CLI. No Bun, no Docker.
- `wrangler.jsonc` holds production at the top level and the preview Worker under `env.preview`. Bindings, `vars`, and `routes` must be repeated under `env.preview`: a preview that inherits the production route or database would touch production. It stays plain JSON with no comments, because scripts edit it.
- A binding is added to `wrangler.jsonc` by the feature that first uses it, in both environments.
- This repo holds no schema. Migrations and their runner are in `andexor-carmen-main` (`db/carmen/`, `tools/migrate.py prev|prod`). Runtime code never issues Data Definition Language (DDL).
- Both hostnames are behind Cloudflare Access, so a plain `curl` gets a sign-in redirect or 403. Use `./health.sh`, which sends the service token from `.env`. The token is allowed on the preview only.
- Scripts are listed in `README.md`. `preview-test.sh` is the smoke test; each feature adds its calls to it.
- A manual step on Cloudflare or GitHub goes in `MANUAL-TASKS.md`.
- Roles are enforced, deny by default (`specs/001-skeleton-and-pipeline/spec.md`). Every endpoint goes in `src/app.py` with `@action(READ)`, `WRITE`, `DELETE`, or `MANAGE` under the route decorator; a route without one is refused with 403 and fails `./unit-test.sh`. List the action of each endpoint in every feature spec.
- The identity of the caller is `request.state.identity` (the `app_user` row plus `machine`). A service token is a machine caller: `ADMIN` on the preview only, never in production, and it has no `app_user` row.
- The Workers runtime returns a D1 row as a Python dict, and no row as a false value. Do not call `to_py()` on it.
- More D1 facts, proven in feature 002: `bind(None)` stores NULL; `all()` returns a dict with a `results` list; `batch([...])` takes a Python list, runs as one transaction, and returns one such dict per statement; `RETURNING *` works. D1 enforces foreign keys by itself, so no `PRAGMA foreign_keys` is issued.
- A delete that a `MEMBER` may perform on their own record declares `@action(WRITE)` and then calls `may_delete(request, owner_id)` (`specs/003-activities-and-tasks/spec.md`, FR-017). Every other delete declares `@action(DELETE)`.
- Dates are put in ISO 8601 form before they are stored, in every endpoint: `records.clean` does it for any body field named `*_date` or `*_at`. Pass a date query filter through `records.iso_date` or `records.iso_timestamp` yourself.
- All score arithmetic is in `src/scoring.py`, which is pure. A feature that changes answers, the rubric, or a service stores scores through `score_writes` or `rescore_all` in `src/app.py`, never with its own SQL.
- Every edit and every delete of a stored record is recorded (`specs/018-change-log/spec.md`): the Worker log first, then the company's timeline. `audit(...)` writes the log line when it is called and returns the timeline statements, which go first in the same `batch` as the change. It returns none when the record has no company, and then the Worker log is the only record. A stage or score change calls `log_change(...)` and writes its own timeline entry. A new endpoint that changes or deletes a record MUST do the same.
- A trigger that runs after a write (a deal's `closed_at`) is not visible in `RETURNING`. Read the row back after the batch.
- Python inside a Worker has no time zone data of its own. The `tzdata` package supplies it, so `zoneinfo` works the same in a Worker and in the unit tests. Use `zoneinfo` in pure code (`records.local_date`), never the Worker's clock zone or the runtime's `Intl`.
- Bytes given to a binding (R2 `put`) MUST be a JavaScript `ArrayBuffer`: `to_js(data).buffer`. The Workers Python layer checks every value passed to a binding and walks anything iterable one item at a time, so a `Uint8Array` or `bytes` of 1 MiB costs about 4 seconds and larger ones exceed the CPU limit. Measured in feature 008.
- Documents: the R2 object is written first, then the row; the row is deleted first, then the object. A company with agreements or documents cannot be deleted, so a test removes those first.
- Work that outlasts a request goes through the `RUNS` queue, never `wait_until`: work started after a response is cut off at about 30 seconds, and a queue consumer ran 200 seconds (discovery D2, `specs/009-ai-runs/spec.md`). The queue handler is in `src/worker.py`; it has no request, so it uses `env` directly and must not raise.
- A buffered response is not sent until the ASGI application returns, so FastAPI `BackgroundTasks` delay the response. Do not use them.
- A new AI provider is one adapter class in `src/ai.py`, registered in `ADAPTERS`. Everything a model returns goes through `clean_facts` or `clean_answers` before it is stored.
- The smoke test and the pipeline MUST NOT call a paid model. They use the stub. A real model is tried on the preview with `./ai-test.sh run <model id>`, on demand; it costs money, so run it when an adapter, a prompt, or a model changed, and say what it cost.
- A provider's API key is a Secrets Store binding (`await env.CLAUDE_API_KEY.get()`), bound in both environments. It is never logged, stored, or returned, and a provider's error message stays in the log.
- Prompts are files in `src/prompts/`, packaged by the `rules` entry in `wrangler.jsonc` and read with `ai.render`. Text from outside (company records, dossiers, web pages) goes between tags and is declared to be data, not instructions.
- A run MUST NOT fall back to another model silently: runs are compared per model, so a refusal fails the run.
- Writes reuse the helpers in `src/app.py` (`insert`, `update`, `unknown_code`, `failed`, `db_status`, and `page` for lists; pass `stamp=False` to `update` for a table with no `updated_at`) and the field lists in `src/records.py`. A write that needs two statements uses `batch`. Each database call sits in its own inline try/except that names its step.
- All code passes strict type checking (`pyrightconfig.json`). Annotate every parameter and return value. JavaScript objects from the Workers runtime are typed `Any`, and the `js` module is reached through `access.js_module()`, because it exists only inside a Worker.
- Run `./unit-test.sh` for the type check and the unit tests. For a local run, put `DEV_USER_EMAIL=<email>` in `.dev.vars`; change the email to act as another user, and that user's role in the local database to test a role.
