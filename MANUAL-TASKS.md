<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins <ed@andexor.net>
-->

# Manual tasks

Setup steps for `andexor-carmen-api` that a person must do on a platform such as Cloudflare or GitHub, and cannot be scripted. Mark a step `[x]` with the date when it is finished. The steps shared by the Carmen family (DNS, Cloudflare Access, the service token, Secrets Store, email) are in `andexor-carmen-main/MANUAL-TASKS.md` and are done.

## This machine

- [X] Copy `.env.example` to `.env` and fill in the Client ID and Client Secret of the Cloudflare Access service token `andexor-carmen-api`. `health.sh`, `preview-test.sh`, and `deploy.sh` need it.
- [X] For local runs, create `.dev.vars` with the line `DEV_USER_EMAIL=<your email>` (see `setup.md`).

## Cloudflare

- [X] Enable R2 on the account (dash.cloudflare.com, R2 Object Storage). Checked 2026-11-22: the API answers "Please enable R2 through the Cloudflare Dashboard", so no bucket can be created yet. Needed before feature 008 (agreements and documents).
- [X] Check the permissions of this repo's Account API token (Manage account, Account API tokens). It needs Workers Scripts Edit, Workers Routes Edit on the `andexor.net` zone, D1 Edit, Workers R2 Storage Edit, Workers AI Read, Account Secrets Store Edit, and Queues Edit if discovery task D2 chooses a queue. The token is in GitHub secrets, and its permissions were never checked.
- [X] Only when a page at another origin must call the preview API from a browser (for example the UI running on your machine): in Zero Trust, Access, Applications, open the application for `carmen-preview.andexor.net`, and under the Cross-Origin Resource Sharing (CORS) settings turn on "Bypass options requests to origin". A browser sends its preflight request with no sign-in, so without this Cloudflare Access answers it and the API never sees it. Then list that origin in `ALLOWED_ORIGINS` under `env.preview` in `wrangler.jsonc`. Not needed for the deployed UI, which is served from the same hostname as the API.
- [ ] Check that this repo's Account API token (the one the pipeline uses) can deploy a Worker bound to Secrets Store: it needs the Secrets Store permission (read is enough). Feature 010 binds `CLAUDE_API_KEY` in both environments. If the check `Preview and smoke test` fails at the deploy step with a Secrets Store error, add the permission to the token.

## GitHub

- [x] (found active 2026-11-22) Default-branch ruleset `andexor-default-ruleset` with the required check `Preview and smoke test`.

## First sign-in

The first user in an empty database becomes the `ADMIN`. Only you are on the Cloudflare Access allow-list, so only you can be first, but do it before adding anyone else.

- [X] After feature 001 is deployed, open `https://carmen.andexor.net/api/v1/me` and check that it shows your email and `"role": "ADMIN"`.
- [X] Do the same at `https://carmen-preview.andexor.net/api/v1/me`.

## Google PageSpeed Insights key (for later)

A reminder, asked for on 2026-11-24. Do this when the PageSpeed Insights feature is taken up; nothing needs the key until then. The steps were written on 2026-11-24 from Google's page "Get Started with the PageSpeed Insights API" (`https://developers.google.com/speed/docs/insights/v5/get-started`). Google changes its console now and then, so read that page again first.

Why a key: the API is free, but a call without a key shares one pool with everyone, and on 2026-11-24 that pool was used up (status 429). With a key the limit is the project's own, about 25,000 requests a day by third-party guides; the exact numbers are shown in the console once the API is enabled.

- [ ] In the Google Cloud Console (`https://console.cloud.google.com`), pick a project or create one for Carmen.
- [ ] Go to APIs & Services, then Library. Search for "PageSpeed Insights API" and choose Enable.
- [ ] Go to APIs & Services, then Credentials. Choose Create credentials, then API key.
- [ ] Edit the new key. Under API restrictions, choose Restrict key and select only "PageSpeed Insights API". Leave application restrictions at None: the calls come from a Cloudflare Worker, which has no fixed address.
- [ ] Store the key in the Cloudflare Secrets Store, in the same store as the other two keys, under the name `carmen-pagespeed-api-key`. It sits beside `carmen-claude-api-key` and `carmen-typesafe-api-key`. Do not put it in a file, in GitHub, or in `wrangler.jsonc`.
- [ ] Tell Claude that the key is stored. The feature then binds it as `PAGESPEED_API_KEY` in production and in the preview.

No billing account should be needed for this API. If the console asks for one, stop and check before adding a card.

## Later, in the running app

- [X] Apply the migration that sets `ai.research.max_pages` to 16 (`specs/019-research-max-pages/spec.md`). Applied to the preview and production on 2026-11-24.
- [X] Merge the pull request for that migration in `andexor-carmen-main` (#23), unchanged: it is already applied.
- [X] After feature 012 (TypeSafe adapter): enable the Jev model. Done by the migration `0005_enable_jev` in `andexor-carmen-main`, applied to the preview and production on 2026-11-24, so no manual step is needed.
- [X] Merge the pull request for that migration in `andexor-carmen-main` (#25), unchanged: it is already applied. Merged on 2026-11-24.
- [X] Merge the migration pull requests in `andexor-carmen-main`: #27, #29, #31, and #33. Merged on 2026-11-24.
- [X] Apply the four migrations (`0006` to `0009`) to production. Applied on 2026-11-24; the preview and production are both at schema version 9. Production held no companies and no answers at the time.
- [X] Recompute the stored scores in production. Not needed: production held no companies and no answers when the Need direction changed (checked on 2026-11-24).
- [ ] Feature 015 (scheduler and notifications): confirm that the test alert sent from the preview on 2026-11-24, "Renewal: Test agreement from the Carmen preview", reached the inbox.
- [ ] After the first morning in production with something to report, confirm that the digest reached the inbox. That closes master question Q5.
- [ ] After the first one-time agreement is completed in production, confirm that the proposals email reached the inbox and that its research is from that day (`specs/025-service-proposals/spec.md`).

## A service token for production (for later)

Not needed now. The Contact Us service is a Worker and calls Carmen over a Service Binding, which needs no token. Do this when a caller that is not a Worker, such as Andexor Grass Spider, has to reach production (`specs/014-machine-methods/spec.md`).

- [ ] In Cloudflare Zero Trust, under Access, Service credentials, create a service token named for the caller.
- [ ] In the production Access application for Carmen, add a policy with the action Service Auth that includes only that token.
- [ ] Give the token's client id and client secret to the caller as secrets. Never commit them.
- [ ] Do not set `SERVICE_TOKENS_ARE_ADMIN` for production. Without it the token may call `POST /api/v1/machine/leads` and `POST /api/v1/machine/facts` and nothing else.

