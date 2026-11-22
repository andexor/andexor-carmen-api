<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Install Pre-requisites

What a development machine needs to work on `andexor-carmen-api`.

## NodeJS

The Cloudflare CLI (`cf`) and Wrangler run on NodeJS. `pywrangler` starts Wrangler through `npx`, so Wrangler itself needs no install.

The following script was copied from
[Node.js — Download Node.js®](https://nodejs.org/en/download/current).

### Download and install nvm:

> `curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash`<br/>
> `exit`

### Start a new shell and download and install Node.js:

> `nvm install 26`

### Verify the Node.js version:

> `node -v`

### Verify npm version:

> `npm -v`

## uv and Spec Kit

Python, the dependencies, and `pywrangler` are managed with uv. Spec Kit manages feature requirements.

> Run `install-uv.sh`<br/>
> Run `install-spec-kit.sh`

## Project dependencies

Installs everything in `pyproject.toml` and writes the license report and the vulnerability report to `reports/`.

> Run `./setup.sh`

## The governing repo

Clone `andexor-carmen-main` beside this repo. The migrations and the migration runner live there, and `preview-create.sh`, `preview-test.sh`, and `deploy.sh` call `../andexor-carmen-main/tools/migrate.py`. Set `CARMEN_MAIN` if it is somewhere else.

## Cloudflare

If you do not have Cloudflare installed and integrated with Claude Code,
then open Claude Code and paste this text into the prompt.

> Fetch and execute the appropriate instructions<br/>
> to set me up for Cloudflare from<br/>
> https://developers.cloudflare.com/agent-setup/prompt.md

Install or upgrade the Cloudflare CLI.

> Run `install-cf.sh`

Then log in. Use the browser-callback flow, not the default device-code flow.

> Run `cf auth login --no-device`

## Cloudflare Access service token

Both Carmen hostnames sit behind Cloudflare Access. `health.sh` signs in to the preview with the service token `andexor-carmen-api`.

> Copy `.env.example` to `.env` and fill in the Client ID and the Client Secret.

`.env` is git-ignored. Never commit the token.

## Local runs

A local run has no Cloudflare Access in front of it. To act as a user, create `.dev.vars` (git-ignored) with one line:

> `DEV_USER_EMAIL=<your email>`

Then run `./run.sh`. The first user in the local database is an `ADMIN`, and every later one is a `READER`. Never put `DEV_USER_EMAIL` in `wrangler.jsonc`.

## PlantUML

Only needed to draw diagrams in `doc/uml`. It needs Java.

> Run `install-plantuml.sh`<br/>
> Run `plantuml.sh`
