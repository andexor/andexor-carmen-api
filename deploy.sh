#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# Production deploy by hand. The normal path is the GitHub Actions pipeline.
#
# Production deploys only after the preview deploy and its smoke test pass (constitution
# Principle IV), so this runs preview-test.sh first and stops if it fails.
#
# Usage: ./deploy.sh

set -euo pipefail

cd "$(dirname "$0")"

./preview-test.sh

echo "Applying migrations to the production database..."
python3 "${CARMEN_MAIN:-../andexor-carmen-main}/tools/migrate.py" prod

echo "Deploying to production..."
uv run pywrangler deploy

# Access refuses the service token on production by design, so this reports and does not fail the deploy.
./health.sh production || true
