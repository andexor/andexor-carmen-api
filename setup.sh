#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# Installs the dependencies in pyproject.toml and writes the license and vulnerability reports.
# Add a dependency with "uv add <name>" (or "uv add --dev <name>"), then run this again.
# Exits non-zero when the audit finds a known vulnerability.

set -euo pipefail

cd "$(dirname "$0")"

mkdir -p reports
uv --quiet sync

# License report. https://pypi.org/project/pip-licenses/
{
    echo
    date --iso-8601=seconds
    echo
    uv run pip-licenses --format=markdown
} > reports/license-report.txt

# Known vulnerabilities in dependencies.
{
    echo
    date --iso-8601=seconds
    echo
} > reports/uv-audit-report.txt
uv --preview-features audit-command audit >> reports/uv-audit-report.txt 2>&1
