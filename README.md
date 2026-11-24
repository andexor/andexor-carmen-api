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
src/worker.py        the Worker entry point
src/app.py           the FastAPI application: identity, role check, endpoints
src/records.py       what a caller may write to each kind of record, the date rule, and the domain rule
src/scoring.py       the scoring engine: points, Fit, Need, confidence, recommendation
src/ai.py            AI adapters (stub, Claude), and the cleaning of what a model returns
src/prompts/         the prompts a model is given, as files with {{name}} placeholders
src/pages.py         reads a company's own website for a model that cannot search
src/access.py        Cloudflare Access token verification
src/roles.py         who may do what
tests/               unit tests, run by unit-test.sh
.github/workflows/   the pipeline: preview and smoke test, then production
wrangler.jsonc       the production Worker, and the preview Worker under env.preview
pyproject.toml       dependencies, managed with uv
pyrightconfig.json   strict type checking, for Pylance and for unit-test.sh
reports/             license and vulnerability reports, written by setup.sh
.specify/            Spec Kit; memory/constitution.md is a copy, do not edit it here
```

## Environments

| | Production | Preview |
| --- | --- | --- |
| Worker | `andexor-carmen-api` | `andexor-carmen-api-preview` |
| Route | `carmen.andexor.net/api/*` | `carmen-preview.andexor.net/api/*` |
| D1 database | `andexor-carmen-api-prod` | `andexor-carmen-api-prev` |
| R2 bucket for documents (`DOCS`) | `andexor-carmen-api-prod` | `andexor-carmen-api-prev` |
| Queue for AI runs (`RUNS`) | `andexor-carmen-api-runs-prod` | `andexor-carmen-api-runs-prev` |

Nothing is served from `workers.dev`.

## Scripts

| Script | Does |
| --- | --- |
| `setup.sh` | Installs the dependencies and writes the license and vulnerability reports |
| `unit-test.sh` | Strict type check (Pyright, as Pylance uses), then the unit tests: roles, token claims, record rules, the scoring engine, allowed origins, every route declares an action |
| `run.sh` | Local run at `http://localhost:8787`, with a local database built from the migrations. Acts as the user named by `DEV_USER_EMAIL` in `.dev.vars` |
| `preview-create.sh` | Finds or creates the preview database, bucket, and queue, and applies the migrations. Safe to run again |
| `preview-test.sh` | Runs the unit tests, migrates the preview database, deploys the preview Worker, and smoke tests it through Cloudflare Access |
| `ai-test.sh` | Tries a real AI model on the preview, on demand: list, enable, or disable models, or run one (Claude, Workers AI, or Jev with `--research`). `run` costs money. Not part of the pipeline |
| `preview-delete.sh` | Deletes the preview Worker, the preview database, and the preview bucket if it is empty. Never touches production |
| `health.sh` | Health check through Cloudflare Access: `./health.sh [preview\|production] [tries]` |
| `deploy.sh` | Production deploy by hand: runs `preview-test.sh` first, then migrates and deploys production |

The migrations and their runner are in `andexor-carmen-main` (`db/carmen/`, `tools/migrate.py`), which must be cloned beside this repo.
