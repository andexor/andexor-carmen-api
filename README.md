# Andexor Carmen API

The back-end services for Andexor Carmen, the Customer Relationship Management (CRM) system for Andexor Network. FastAPI on Cloudflare Python Workers with D1 and R2, served at `carmen.andexor.net/api/*`, behind Cloudflare Access.

- Requirements: [`PRD.md`](PRD.md), governed by the master Product Requirements Document (PRD) in `andexor-carmen-main`. The PRD is frozen. Later decisions are in [`specs/`](specs/), and where they differ from the PRD, the spec governs.
- Conventions: [`CLAUDE.md`](CLAUDE.md).
- Constitution: `.specify/memory/constitution.md`, copied from `andexor-carmen-main`.
- Machine setup: [`setup.md`](setup.md). Manual platform steps: [`MANUAL-TASKS.md`](MANUAL-TASKS.md).

Work follows Spec Kit. The feature backlog is section 13 of the PRD.

```
PRD.md               what this app must do (frozen)
specs/               Spec Kit specifications; 000-repo-setup records what changed since the PRD
src/worker.py        the Worker (health only until feature 001)
.github/workflows/   the pipeline: preview and smoke test, then production
wrangler.jsonc       the production Worker, and the preview Worker under env.preview
pyproject.toml       dependencies, managed with uv
reports/             license and vulnerability reports, written by setup.sh
.specify/            Spec Kit; memory/constitution.md is a copy, do not edit it here
```

## Environments

| | Production | Preview |
| --- | --- | --- |
| Worker | `andexor-carmen-api` | `andexor-carmen-api-preview` |
| Route | `carmen.andexor.net/api/*` | `carmen-preview.andexor.net/api/*` |
| D1 database | `andexor-carmen-api-prod` | `andexor-carmen-api-prev` |

Nothing is served from `workers.dev`.

## Scripts

| Script | Does |
| --- | --- |
| `setup.sh` | Installs the dependencies and writes the license and vulnerability reports |
| `run.sh` | Local run at `http://localhost:8787` |
| `preview-create.sh` | Finds or creates the preview database and applies the migrations. Safe to run again |
| `preview-test.sh` | Migrates the preview database, deploys the preview Worker, and smoke tests it through Cloudflare Access |
| `preview-delete.sh` | Deletes the preview Worker and the preview database. Never touches production |
| `health.sh` | Health check through Cloudflare Access: `./health.sh [preview\|production] [tries]` |
| `deploy.sh` | Production deploy by hand: runs `preview-test.sh` first, then migrates and deploys production |

The migrations and their runner are in `andexor-carmen-main` (`db/carmen/`, `tools/migrate.py`), which must be cloned beside this repo.
