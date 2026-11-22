<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Manual tasks

Setup steps for `andexor-carmen-api` that a person must do on a platform such as Cloudflare or GitHub, and cannot be scripted. Mark a step `[x]` with the date when it is finished. The steps shared by the Carmen family (DNS, Cloudflare Access, the service token, Secrets Store, email) are in `andexor-carmen-main/MANUAL-TASKS.md` and are done.

## This machine

- [X] Copy `.env.example` to `.env` and fill in the Client ID and Client Secret of the Cloudflare Access service token `andexor-carmen-api`. `health.sh`, `preview-test.sh`, and `deploy.sh` need it.
- [ ] For local runs, create `.dev.vars` with the line `DEV_USER_EMAIL=<your email>` (see `setup.md`).

## Cloudflare

- [ ] Enable R2 on the account (dash.cloudflare.com, R2 Object Storage). Checked 2026-11-22: the API answers "Please enable R2 through the Cloudflare Dashboard", so no bucket can be created yet. Needed before feature 008 (agreements and documents).
- [ ] Check the permissions of this repo's Account API token (Manage account, Account API tokens). It needs Workers Scripts Edit, Workers Routes Edit on the `andexor.net` zone, D1 Edit, Workers R2 Storage Edit, Workers AI Read, Account Secrets Store Edit, and Queues Edit if discovery task D2 chooses a queue. The token is in GitHub secrets, and its permissions were never checked.

## GitHub

- [x] (found active 2026-11-22) Default-branch ruleset `andexor-default-ruleset` with the required check `Preview and smoke test`.

## First sign-in

The first user in an empty database becomes the `ADMIN`. Only you are on the Cloudflare Access allow-list, so only you can be first, but do it before adding anyone else.

- [ ] After feature 001 is deployed, open `https://carmen.andexor.net/api/v1/me` and check that it shows your email and `"role": "ADMIN"`.
- [ ] Do the same at `https://carmen-preview.andexor.net/api/v1/me`.

## Later, in the running app

- [ ] After feature 012 (TypeSafe adapter): enable the Jev model in Settings.
- [ ] After feature 015 (scheduler and notifications): send one real digest from the running Worker and confirm it reaches the inbox. That closes master question Q5.
