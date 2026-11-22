#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# The type check, then the unit tests. No Worker and no second user is needed.
#
# The type check is Pyright in strict mode (pyrightconfig.json), the checker behind Pylance, so the
# command line and the editor agree. The version is pinned; raise it on purpose.

set -euo pipefail

cd "$(dirname "$0")"

echo "Type check..."
npx --yes pyright@1.1.414

echo "Unit tests..."
uv run python -m unittest discover -s tests "$@"
