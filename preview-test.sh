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
trap 'rm -f "${BODY}"' EXIT

# expect <status> <text the body must contain> <curl arguments...>
expect() {
    local want=$1 text=$2 status
    shift 2
    status=$(curl -s -o "${BODY}" --write-out '%{http_code}' \
        -H "CF-Access-Client-Id: ${ACCESS_CLIENT_ID}" \
        -H "CF-Access-Client-Secret: ${ACCESS_CLIENT_SECRET}" "$@")
    if [[ "${status}" != "${want}" ]] || ! grep -q "${text}" "${BODY}"; then
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
