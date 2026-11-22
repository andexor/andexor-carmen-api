#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# Unit tests. No Worker, no network, and no second user is needed.

set -euo pipefail

cd "$(dirname "$0")"

uv run python -m unittest discover -s tests "$@"
