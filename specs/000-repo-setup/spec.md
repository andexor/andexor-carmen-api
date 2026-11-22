<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Feature Specification: Repository setup

**Feature Branch**: `1-write-prd`

**Created**: 2026-11-22

**Status**: Implemented.

**Input**: Issue #1. Bring this repo in line with `andexor-carmen-main` before the first feature: constitution 2.1.0, tooling, scripts, and a record of everything decided since this repo's Product Requirements Document (PRD) was written on 2026-11-15. The PRD is frozen. Where this spec and the PRD differ, this spec governs.

This spec is numbered `000` so that the Spec Kit folders `001` to `016` carry the same numbers as the backlog in PRD section 13.

## Change 1: tooling

### Requirements

- **FR-001**: The repo MUST NOT contain Bun, Docker, or RabbitMQ scripts or instructions. `install-bun.sh`, `install-docker.sh`, `install-rabbitmq-server.sh`, `build.sh`, and `debug.sh` are removed. "Docker packaging" is removed from PRD section 12. The one remaining mention is the constitution's rule against Bun and Docker, which is copied unchanged.
- **FR-002**: The constitution, `CONTRIBUTING.md`, `SECURITY.md`, `install-cf.sh`, `install-plantuml.sh`, `plantuml.sh`, and `.prettierignore` are copies of the files in `andexor-carmen-main`.
- **FR-003**: `pyproject.toml` starts at version `1.0.0`, with `fastapi` as the only runtime dependency and `workers-py`, `workers-runtime-sdk`, and `pip-licenses` as development dependencies. A dependency is added by the feature that first needs it.
- **FR-004**: `wrangler.jsonc` holds production at the top level and the preview Worker `andexor-carmen-api-preview` under `env.preview`. `vars`, `routes`, and every binding MUST be repeated under `env.preview`, because Wrangler lets an environment inherit `routes`, and a preview that inherits the production route would take production traffic. A binding is added by the feature that first uses it, in both environments.

## Change 2: scripts

These replace the list in PRD section 9.

### Requirements

- **FR-005**: `setup.sh`, `run.sh`, `health.sh`, `deploy.sh`, `preview-create.sh`, `preview-test.sh`, and `preview-delete.sh` exist at the root, as in `andexor-net-website-ping`, and read every name from `wrangler.jsonc`.
- **FR-006**: This repo holds no copy of the schema and no migration runner. The scripts call `tools/migrate.py prev|prod` in a checkout of `andexor-carmen-main` (beside this repo, or at `CARMEN_MAIN`). This replaces `scripts/sync_schema.sh`, `schema.lock`, `scripts/migrate.py`, and `scripts/create_env.py` in PRD sections 3 and 9. The runner was written in `andexor-carmen-main` after the PRD (its spec 005), and one runner cannot disagree with itself. The pipeline (feature 001) checks out `andexor-carmen-main` to run it.
- **FR-007**: Every request from a script to either hostname MUST carry the Cloudflare Access service token in the `CF-Access-Client-Id` and `CF-Access-Client-Secret` headers. The token is read from `ACCESS_CLIENT_ID` and `ACCESS_CLIENT_SECRET`, in the environment or in a git-ignored `.env`. These are the names of the GitHub secrets, so the pipeline and a person run the same scripts.
- **FR-008**: `preview-test.sh` is the smoke test: migrate the preview database, deploy the preview Worker, health check with retries. Each feature adds its calls to it, and the pipeline runs this script.
- **FR-009**: `deploy.sh` MUST run `preview-test.sh` first and stop if it fails (constitution Principle IV).
- **FR-010**: `preview-delete.sh` MUST refuse to run when a preview name or database id equals a production one, and MUST ask for confirmation unless `--yes` is given. There is no production teardown.
- **FR-011**: The preview bucket and the stub model are added to `preview-create.sh` by the features that introduce them (008 and 009).

## Change 3: where the PRD is out of date

Decisions made after 2026-11-15. Each row names what governs.

| PRD says | What governs now | Source |
| --- | --- | --- |
| Section 4: roles are recorded, not enforced. Section 12: role enforcement is out of scope | Roles are enforced. See Change 5 | Constitution 2.1.0, Principle VI |
| Section 4: the user's email comes from the token | A service-token request carries no email. Feature 001 MUST define the identity of a machine caller | Main decisions, R3 |
| Section 8: notifications ship as a stub only | Email is sent with Cloudflare Email Sending through the `send_email` binding, from `carmen@carmen.andexor.net`, to Ed only | Main decisions, Q5 |
| Section 7.2: the run is written with `duration_ms` | Code never writes `ai_run.duration_ms` or `deal.total_deal_time`; triggers set them. Code writes `started_at` and `finished_at` as UTC with milliseconds, and `model_ms` | `db/README.md` |
| Section 7: token counts per run | Also fill `ai_run.search_count`, and cap web searches per run | Main decisions, R6 |
| Section 7.1: probability | `ai_run_answer.probability` is the model's probability that the answer is correct. It is not confidence, which is the run's coverage of the rubric | Main decisions |
| Section 7.2: unparseable output is retried once | Also: output MUST be parsed strictly into fixed fields, and output that does not match the schema is rejected | Main decisions, R7 |
| Sections 5.5 and 5.9: accept all facts; import | These writes MUST be batched, and the D1 per-query limits checked when the features are specified | Main decisions, R4 |
| Section 2: `TYPESAFE_API_KEY` bound "when available" | The key is in Secrets Store as `carmen-typesafe-api-key`. Jev is enabled in Settings once the adapter exists | Main decisions, Q3 |
| Section 10: the pipeline is recorded in `CICD.md` | No longer required | Constitution 2.1.0 |

## Change 4: a real required check, pulled forward from feature 001

The default-branch ruleset already requires the check `Preview and smoke test`, so nothing can merge without a workflow that provides it. Decided 2026-11-22: add the smallest real one now. Feature 001 builds on it.

### Requirements

- **FR-012**: `src/worker.py` serves only `GET /api/v1/health`, which returns 200 `{"ok": true}` and touches no dependency.
- **FR-013**: `.github/workflows/ci.yml` has a job named `Preview and smoke test` that checks out `andexor-carmen-main`, installs `cf` and the dependencies, and runs `./preview-test.sh`. On a push to `main`, a second job migrates the production database and deploys production.
- **FR-014**: The production job has no HTTP check, because Cloudflare Access refuses the service token on production by design. How production is checked after a deploy is an open question for feature 001.
- **FR-015**: `.github/CODEOWNERS` and `.github/dependabot.yml` are as in `andexor-net-website-ping`.

## Change 5: roles are enforced from the first protected endpoint

Decided 2026-11-22. Version 1 has one user, an `ADMIN`, so nothing would show a missing check until the day a second person signs in. Enforcement is therefore built and tested before any other user exists. This replaces "recorded, not enforced" in PRD section 4 and removes role enforcement from PRD section 12.

### Requirements

- **FR-016**: Role enforcement ships in feature 001, together with Cloudflare Access verification. No endpoint other than `GET /api/v1/health` may exist without it.
- **FR-017**: The rule is one pure function, `allowed(role, action)`, with no input or output, so it can be tested without a Worker or a second user. `READER` may read. `MEMBER` may read, create, and update records, and may not delete anything or change users, settings, the rubric, the service catalog, or the AI model list. `ADMIN` may do anything.
- **FR-018**: Deny by default. Every route MUST declare the action it performs. A route that declares none, a role the function does not know, and a user with no `app_user` row are all refused with 403 `FORBIDDEN`.
- **FR-019**: The role is read from `app_user` on every request. It is never taken from the token, a header, or the request body, and it is never cached across requests, so a demotion takes effect at once.
- **FR-020**: A new user's role is whatever the database gives it (`READER`, by the trigger and default in the data model). The API MUST NOT set a role when it creates the `app_user` row on first sign-in.
- **FR-021**: A machine caller (service token or Service Binding) has no role by default. Feature 001 MUST name what each machine caller may do. None is treated as `ADMIN` by omission.
- **FR-022**: Unit tests, run in the pipeline before the preview deploy, MUST cover every role against every action, and MUST fail when any route in the application has no declared action. A new endpoint therefore cannot ship unprotected.
- **FR-023**: Each feature's specification MUST list, for each endpoint it adds, the lowest role that may call it.

### What is not tested

No second person is onboarded, so no test signs in as a real `MEMBER` or `READER` through Cloudflare Access. The tests call the rule and the routes with simulated users of each role. The first real sign-in by a second person is the first end-to-end test, and that person starts as `READER`.

## Change 6: the FastAPI pin moves up one version at a time

Decided 2026-11-22, issue #4. `fastapi<0.163` was copied from `andexor-net-website-ping`, where it was set because Cloudflare Python Workers did not run FastAPI 0.163 or later. They now run 0.163. This replaces the pin in FR-003.

### Requirements

- **FR-024**: `pyproject.toml` lists `fastapi<0.164`. The upper bound stays, one minor version above the version that is proven on a Worker, and Dependabot proposes each move.
- **FR-025**: A FastAPI upgrade is accepted only when the check `Preview and smoke test` passes with it, because that check deploys the new version to a real Python Worker.

Checked 2026-11-22: FastAPI 0.163.0 deploys to the preview Worker and `GET /api/v1/health` returns 200, from a development machine and in the pipeline.

## Change 7: the pipeline's actions run on Node.js 24

Decided 2026-11-22. The action versions copied from `andexor-net-website-ping` were built for Node.js 20, which GitHub has deprecated, so every run carried a warning.

### Requirements

- **FR-026**: `.github/workflows/ci.yml` uses `actions/checkout@v7.0.1`, `actions/setup-node@v7.1.0`, and `astral-sh/setup-uv@v10.4.0`, each of which declares Node.js 24.
- **FR-027**: `.github/dependabot.yml` also watches the `github-actions` ecosystem, weekly, so a later release arrives as a pull request and is accepted only when the check `Preview and smoke test` passes with it.

## Success criteria

- **SC-001**: `.specify/memory/constitution.md` is identical to the file in `andexor-carmen-main` (version 2.1.0).
- **SC-002**: A search of the repo for Bun, Docker, and RabbitMQ finds only the constitution's rule.
- **SC-003**: `./setup.sh` exits 0 and reports no known vulnerabilities.
- **SC-004**: `./health.sh preview` answers 200 through Cloudflare Access with the service token, the same request without the token is redirected to the sign-in page, and `./health.sh production` is refused with 403. Checked 2026-11-22, which settles discovery task D3 from a development machine.
- **SC-006**: The check `Preview and smoke test` passes on the pull request.
- **SC-005**: `./preview-create.sh` run twice changes nothing the second time.
