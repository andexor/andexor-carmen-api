#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# Local run at http://localhost:8787, with a local database. Cloudflare Access is not in front of it.
uv run pywrangler dev
