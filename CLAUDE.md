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
- Roles are enforced, deny by default: every route declares its action, and a test fails if one does not (`specs/000-repo-setup/spec.md`, Change 5). List the lowest allowed role for each endpoint in every feature spec.
