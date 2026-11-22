#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# Local run at http://localhost:8787, with a local database. Cloudflare Access is not in front of
# it, so put DEV_USER_EMAIL=<your email> in .dev.vars (git-ignored) to act as that user. Without
# it every path but /api/v1/health answers 401.

set -euo pipefail

cd "$(dirname "$0")"

# shortcut: the local database is built once, from every migration. After a new migration, delete
# .wrangler/state and run this again.
if [[ ! -d .wrangler/state/v3/d1 ]]; then
    DB_NAME=$(jq -r '.d1_databases[0].database_name' wrangler.jsonc)
    for file in "${CARMEN_MAIN:-../andexor-carmen-main}"/db/carmen/[0-9][0-9][0-9][0-9]_*.sql; do
        [[ "${file}" == *.base.sql ]] && continue
        echo "Applying $(basename "${file}") to the local database"
        uv run pywrangler d1 execute "${DB_NAME}" --local --file "${file}" > /dev/null
    done
fi

uv run pywrangler dev
