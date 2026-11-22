#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# Health check through Cloudflare Access.
#
# Both hostnames sit behind Cloudflare Access, so a plain curl never reaches the Worker. This sends
# the service token "andexor-carmen-api" in the two Access headers. The token is allowed on the
# preview only, so production answers 401 or 403 by design.
#
# The token comes from ACCESS_CLIENT_ID and ACCESS_CLIENT_SECRET, in the environment or in .env
# (git-ignored, see .env.example). The hostname comes from the route in wrangler.jsonc.
#
# Usage: ./health.sh [preview|production] [tries]
#
#   preview|production  Which environment to check. Default: preview.
#   tries               How many times to try, 3 seconds apart. Default: 1.
#
# Requires: curl, jq.

set -euo pipefail

cd "$(dirname "$0")"

TARGET=${1:-preview}
TRIES=${2:-1}

case "${TARGET}" in
    preview) CONFIG_PATH=.env.preview ;;
    production) CONFIG_PATH= ;;
    -h|--help) sed -n '/^# Usage/,/^# Requires/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "error: unknown environment ${TARGET}" >&2; exit 1 ;;
esac

if [[ -f .env ]]; then
    set -a
    . ./.env
    set +a
fi
: "${ACCESS_CLIENT_ID:?set ACCESS_CLIENT_ID in the environment or in .env (see .env.example)}"
: "${ACCESS_CLIENT_SECRET:?set ACCESS_CLIENT_SECRET in the environment or in .env (see .env.example)}"

ROUTE=$(jq -r "${CONFIG_PATH}.routes[0].pattern" wrangler.jsonc)
URL="https://${ROUTE%%/*}/api/v1/health"
BODY=$(mktemp)
trap 'rm -f "${BODY}"' EXIT

for ((i = 1; i <= TRIES; i++)); do
    STATUS=$(curl -s -o "${BODY}" --write-out '%{http_code}' \
        -H "CF-Access-Client-Id: ${ACCESS_CLIENT_ID}" \
        -H "CF-Access-Client-Secret: ${ACCESS_CLIENT_SECRET}" \
        "${URL}" || true)
    echo "${TARGET}: try ${i}: status ${STATUS}"
    if [[ "${STATUS}" == "200" ]] && grep -q '"ok":[[:space:]]*true' "${BODY}"; then
        cat "${BODY}"
        echo
        exit 0
    fi
    [[ ${i} -lt ${TRIES} ]] && sleep 3
done

case "${STATUS}" in
    401|403) echo "Cloudflare Access refused the service token at ${URL}. On production that is by design." ;;
    522) echo "Access let the request in, and no Worker answered at ${URL}. Deploy it first." ;;
esac
echo "Health check failed"
exit 1
