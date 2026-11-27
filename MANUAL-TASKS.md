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
- [X] Check that this repo's Account API token (the one the pipeline uses) can deploy a Worker bound to Secrets Store: it needs the Secrets Store permission (read is enough). Feature 010 binds `CLAUDE_API_KEY` in both environments. If the check `Preview and smoke test` fails at the deploy step with a Secrets Store error, add the permission to the token.

## GitHub

- [x] (found active 2026-11-22) Default-branch ruleset `andexor-default-ruleset` with the required check `Preview and smoke test`.

## First sign-in

The first user in an empty database becomes the `ADMIN`. Only you are on the Cloudflare Access allow-list, so only you can be first, but do it before adding anyone else.

- [X] After feature 001 is deployed, open `https://carmen.andexor.net/api/v1/me` and check that it shows your email and `"role": "ADMIN"`.
- [X] Do the same at `https://carmen-preview.andexor.net/api/v1/me`.

## Google PageSpeed Insights key

Nothing to do in Carmen. Decided on 2026-11-27: Andexor Grass Spider (AGS) holds the key and makes the measurement, so Carmen gets no key and no binding. Ed created the key on 2026-11-27 for AGS. See `NOTES.md`, under "Decisions to revisit".

- [ ] Tell Claude when AGS can measure PageSpeed. Carmen's side (the request to AGS) is specified then.

## Later, in the running app

- [X] Apply the migration that sets `ai.research.max_pages` to 16 (`specs/019-research-max-pages/spec.md`). Applied to the preview and production on 2026-11-24.
- [X] Merge the pull request for that migration in `andexor-carmen-main` (#23), unchanged: it is already applied.
- [X] After feature 012 (TypeSafe adapter): enable the Jev model. Done by the migration `0005_enable_jev` in `andexor-carmen-main`, applied to the preview and production on 2026-11-24, so no manual step is needed.
- [X] Merge the pull request for that migration in `andexor-carmen-main` (#25), unchanged: it is already applied. Merged on 2026-11-24.
- [X] Merge the migration pull requests in `andexor-carmen-main`: #27, #29, #31, and #33. Merged on 2026-11-24.
- [X] Apply the four migrations (`0006` to `0009`) to production. Applied on 2026-11-24; the preview and production are both at schema version 9. Production held no companies and no answers at the time.
- [X] Recompute the stored scores in production. Not needed: production held no companies and no answers when the Need direction changed (checked on 2026-11-24).
- [X] Feature 015 (scheduler and notifications): confirm that the test alert sent from the preview on 2026-11-24, "Renewal: Test agreement from the Carmen preview", reached the inbox.
- [X] After the first morning in production with something to report, confirm that the digest reached the inbox. That closes master question Q5.
- [X] After the first one-time agreement is completed in production, confirm that the proposals email reached the inbox and that its research is from that day (`specs/025-service-proposals/spec.md`).
- [X] 2026-11-26: the name is in the setting `digest.recipient` in both databases, set by the migration `0018_recipient_name` in `andexor-carmen-main` (`specs/028-email-names/spec.md`).

## A service token for production

Nothing to do. Decided on 2026-11-27: Andexor Grass Spider (AGS) will be a Worker and will call Carmen over a Service Binding, as the Contact Us service does. A Service Binding needs no token, so production keeps one Access policy, Allow, and admits no service token. If a caller that is not a Worker ever needs production, the steps are in `specs/014-machine-methods/spec.md`, under Manual steps: a token for that caller and a Service Auth policy that includes only it. `SERVICE_TOKENS_ARE_ADMIN` is never set for production.
