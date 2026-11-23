#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# Runs the unit tests, migrates the preview database, deploys the preview Worker, and smoke tests
# it through Cloudflare Access. Stops at the first failure. Never touches production.
#
# The migrations live in andexor-carmen-main. Set CARMEN_MAIN to its checkout if it is not beside
# this repo.
#
# Usage: ./preview-test.sh
#
# Requires: uv, cf (logged in), python3, and what health.sh requires.

set -euo pipefail

cd "$(dirname "$0")"

echo "Unit tests..."
./unit-test.sh

echo "Applying migrations to the preview database..."
python3 "${CARMEN_MAIN:-../andexor-carmen-main}/tools/migrate.py" prev

echo "Deploying the preview..."
uv run pywrangler deploy --env preview

echo "Health check..."
./health.sh preview 10

# Smoke test. Each feature adds its calls here.
if [[ -f .env ]]; then
    set -a
    . ./.env
    set +a
fi
HOST=$(jq -r '.env.preview.routes[0].pattern' wrangler.jsonc)
BASE="https://${HOST%%/*}/api/v1"
BODY=$(mktemp)
HEAD=$(mktemp)
trap 'rm -f "${BODY}" "${HEAD}"' EXIT

# expect <status> <text the body must contain, or '' for any body> <curl arguments, the URL last>
expect() {
    local want=$1 text=$2 status
    shift 2
    status=$(curl -s -o "${BODY}" -D "${HEAD}" --write-out '%{http_code}' \
        -H "CF-Access-Client-Id: ${ACCESS_CLIENT_ID}" \
        -H "CF-Access-Client-Secret: ${ACCESS_CLIENT_SECRET}" "$@")
    if [[ "${status}" != "${want}" ]] || { [[ -n "${text}" ]] && ! grep -q "${text}" "${BODY}"; }; then
        echo "FAILED: curl $* returned ${status}, expected ${want} with ${text}"
        cat "${BODY}"
        echo
        exit 1
    fi
    echo "ok: ${status} ${!#}"
}

echo "Smoke test..."
# The service token is a machine caller: ADMIN on the preview, and it has no app_user row.
expect 200 '"role":"ADMIN"' "${BASE}/me"
expect 200 '"machine":true' "${BASE}/me"
expect 404 '"error":"NOT_FOUND"' "${BASE}/no-such-path"

# Companies and contacts (specs/002-companies-and-contacts/spec.md). The test company is deleted at
# the end, and at the start if an earlier run failed and left it behind.
JSON=(-H "Content-Type: application/json")
expect 200 '"domain":"COMPANY_STAGE"' "${BASE}/srt"
expect 200 '"items"' "${BASE}/companies?q=smoke-test.invalid"
for id in $(jq -r '.items[].id' "${BODY}"); do
    expect 204 '' -X DELETE "${BASE}/companies/${id}"
done
expect 201 '"domain":"smoke-test.invalid"' -X POST "${JSON[@]}" \
    -d '{"name":"Smoke Test","website":"https://www.Smoke-Test.invalid/about"}' "${BASE}/companies"
ID=$(jq -r '.id' "${BODY}")
# A second company with the same domain is refused, and the answer names the first.
expect 409 "\"company_id\":\"${ID}\"" -X POST "${JSON[@]}" -d '{"website":"smoke-test.invalid"}' "${BASE}/companies"
expect 200 '"stage":"PROSPECT"' -X PATCH "${JSON[@]}" -d '{"stage":"PROSPECT"}' "${BASE}/companies/${ID}"
expect 400 '"error":"BAD_REQUEST"' -X PATCH "${JSON[@]}" -d '{"stage":"NO_SUCH_STAGE"}' "${BASE}/companies/${ID}"
# The database enforces foreign keys with no PRAGMA: an owner that does not exist is refused.
expect 400 '"error":"BAD_REQUEST"' -X PATCH "${JSON[@]}" -d '{"owner_id":"no-such-user"}' "${BASE}/companies/${ID}"
expect 201 '"is_primary":1' -X POST "${JSON[@]}" -d '{"name":"Smoke Contact","is_primary":true}' "${BASE}/companies/${ID}/contacts"
expect 200 '"name":"Smoke Contact"' "${BASE}/companies/${ID}"
expect 200 '"next_cursor":null' "${BASE}/companies?q=smoke+contact"
expect 204 '' -X DELETE "${BASE}/companies/${ID}"
expect 404 '"error":"NOT_FOUND"' "${BASE}/companies/${ID}"

# Cross-Origin Resource Sharing (specs/017-cors/spec.md): only an origin in ALLOWED_ORIGINS is echoed.
# header <yes|no> <text>: the response headers of the last call must, or must not, contain the text.
header() {
    if grep -qi "$2" "${HEAD}"; then found=yes; else found=no; fi
    if [[ "${found}" != "$1" ]]; then
        echo "FAILED: response header '$2' present: ${found}, expected: $1"
        exit 1
    fi
    echo "ok: header $2: $1"
}
ORIGIN=$(jq -r '.env.preview.vars.ALLOWED_ORIGINS' wrangler.jsonc | cut -d, -f1)
expect 200 '"role":"ADMIN"' -H "Origin: ${ORIGIN}" "${BASE}/me"
header yes "^access-control-allow-origin: ${ORIGIN}"
header yes "^access-control-allow-credentials: true"
expect 200 '"role":"ADMIN"' -H "Origin: https://not-allowed.invalid" "${BASE}/me"
header no "^access-control-allow-origin"
# A preflight (OPTIONS) is not checked here: Cloudflare Access answers it with 403 before the API
# sees it, even with the service token, until the setting in MANUAL-TASKS.md is turned on.
