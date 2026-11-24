#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins
# Runs the scheduler on the preview now, with real email, on demand. Never touches production.
#
# The preview normally sends nothing (EMAIL_PROVIDER is STUB), and neither does the pipeline's
# smoke test (preview-test.sh). This script is the real test: it sends today's digest of the
# preview's data, and an alert for each agreement that is due one, to the setting digest.recipient
# of the preview database. Run it when a template or the sending has changed.
#
# Usage:
#   ./email-test.sh
#
# Requires: curl, jq, and the Cloudflare Access service token in .env (see .env.example).
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .env ]]; then
    set -a
    . ./.env
    set +a
fi
HOST=$(jq -r '.env.preview.routes[0].pattern' wrangler.jsonc)
BASE="https://${HOST%%/*}/api/v1"
BODY=$(mktemp)
trap 'rm -f "${BODY}"' EXIT
call() {
    local status
    if ! status=$(curl -s -o "${BODY}" --write-out '%{http_code}' \
        -H "CF-Access-Client-Id: ${ACCESS_CLIENT_ID:?set it in .env}" \
        -H "CF-Access-Client-Secret: ${ACCESS_CLIENT_SECRET:?set it in .env}" "$@"); then
        echo "FAILED: the preview could not be reached"
        exit 1
    fi
    if [[ "${status}" -ge 400 ]]; then
        echo "FAILED: ${!#} returned ${status}: $(cat "${BODY}")"
        exit 1
    fi
}
call -X POST "${BASE}/scheduler/run?email=true"
echo "The run: $(cat "${BODY}")"
call "${BASE}/notifications?limit=5"
echo "The newest notifications:"
jq -r '.items[] | "  \(.created_at)  \(.status)  \(.subject)\(if .error then "  (" + .error + ")" else "" end)"' "${BODY}"
