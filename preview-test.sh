#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# Migrates the preview database, deploys the preview Worker, and smoke tests it through
# Cloudflare Access. Stops at the first failure. Never touches production.
#
# The migrations live in andexor-carmen-main. Set CARMEN_MAIN to its checkout if it is not beside
# this repo.
#
# Usage: ./preview-test.sh
#
# Requires: uv, cf (logged in), python3, and what health.sh requires.

set -euo pipefail

cd "$(dirname "$0")"

echo "Applying migrations to the preview database..."
python3 "${CARMEN_MAIN:-../andexor-carmen-main}/tools/migrate.py" prev

echo "Deploying the preview..."
uv run pywrangler deploy --env preview

echo "Health check..."
./health.sh preview 10

# Each feature adds its smoke test calls here.
