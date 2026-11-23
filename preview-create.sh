#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# Creates the shared preview environment's data, so a preview never touches production data:
#
#   1. The preview D1 database named in env.preview of wrangler.jsonc, if it is missing.
#   2. Its id, written into env.preview of wrangler.jsonc.
#   3. Every pending migration from andexor-carmen-main, applied to it.
#   4. The preview R2 bucket for documents named in env.preview, if it is missing.
#
# The preview Worker and its route (carmen-preview.andexor.net/api/*) are created by the deploy
# in preview-test.sh. The DNS record, the Cloudflare Access application, and the service token
# are made by hand once (andexor-carmen-main/MANUAL-TASKS.md) and are not touched here.
#
# Safe to run again: every step checks what already exists. Undo it with preview-delete.sh.
#
# Usage: ./preview-create.sh
#
# Requires: cf (logged in), jq, python3. wrangler.jsonc must be plain JSON, with no comments.
# Set CARMEN_MAIN to the andexor-carmen-main checkout if it is not beside this repo.

set -euo pipefail

cd "$(dirname "$0")"

WRANGLER_FILE=wrangler.jsonc

case "${1:-}" in
    "") ;;
    -h|--help) sed -n '/^# Usage/,/^# Set CARMEN_MAIN/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "error: unknown argument $1" >&2; exit 1 ;;
esac

for tool in cf jq python3; do
    command -v "${tool}" > /dev/null || { echo "error: ${tool} is not installed" >&2; exit 1; }
done

jq -e . "${WRANGLER_FILE}" > /dev/null 2>&1 || {
    echo "error: ${WRANGLER_FILE} is not plain JSON (comments are not supported by this script)" >&2
    exit 1
}

PROD_DB_ID=$(jq -r '.d1_databases[0].database_id' "${WRANGLER_FILE}")
PREVIEW_DB_NAME=$(jq -r '.env.preview.d1_databases[0].database_name // empty' "${WRANGLER_FILE}")

if [[ -z "${PREVIEW_DB_NAME}" ]]; then
    echo "error: no env.preview.d1_databases entry in ${WRANGLER_FILE}" >&2
    exit 1
fi

# 1. Find or create the preview database.
DB_ID=$(cf d1 list --name "${PREVIEW_DB_NAME}" 2> /dev/null \
    | jq -r --arg n "${PREVIEW_DB_NAME}" '[.[] | select(.name == $n)][0].uuid // empty')

if [[ -z "${DB_ID}" ]]; then
    echo "Creating D1 database ${PREVIEW_DB_NAME}"
    DB_ID=$(cf d1 create --name "${PREVIEW_DB_NAME}" 2> /dev/null | jq -r '.uuid')
else
    echo "D1 database ${PREVIEW_DB_NAME} already exists"
fi

if [[ -z "${DB_ID}" || "${DB_ID}" == "null" ]]; then
    echo "error: could not determine the id of ${PREVIEW_DB_NAME}" >&2
    exit 1
fi

# Never let the preview environment point at production data.
if [[ "${DB_ID}" == "${PROD_DB_ID}" ]]; then
    echo "error: the preview database id matches the production database id" >&2
    exit 1
fi

# The preview bucket for documents. A preview must never share a bucket with production.
PREVIEW_BUCKET=$(jq -r '.env.preview.r2_buckets[0].bucket_name // empty' "${WRANGLER_FILE}")
PROD_BUCKET=$(jq -r '.r2_buckets[0].bucket_name // empty' "${WRANGLER_FILE}")
if [[ -n "${PREVIEW_BUCKET}" ]]; then
    if [[ "${PREVIEW_BUCKET}" == "${PROD_BUCKET}" ]]; then
        echo "error: the preview bucket name matches the production bucket name" >&2
        exit 1
    fi
    if cf r2 buckets list 2> /dev/null | sed -n '/^{/,$p' \
        | jq -e --arg n "${PREVIEW_BUCKET}" '.buckets[] | select(.name == $n)' > /dev/null; then
        echo "R2 bucket ${PREVIEW_BUCKET} already exists"
    else
        echo "Creating R2 bucket ${PREVIEW_BUCKET}"
        cf r2 buckets create --name "${PREVIEW_BUCKET}" --location-hint enam > /dev/null
    fi
fi

# 2. Write the id into the config.
if [[ "$(jq -r '.env.preview.d1_databases[0].database_id' "${WRANGLER_FILE}")" == "${DB_ID}" ]]; then
    echo "${WRANGLER_FILE} already has the preview database id"
else
    NEW_CONFIG=$(jq --indent 4 --arg id "${DB_ID}" '.env.preview.d1_databases[0].database_id = $id' "${WRANGLER_FILE}")
    echo "${NEW_CONFIG}" > "${WRANGLER_FILE}"
    echo "Wrote the preview database id to ${WRANGLER_FILE}. Commit it."
fi

# 3. Apply the migrations. The runner reads schema_migration, so this is repeatable.
python3 "${CARMEN_MAIN:-../andexor-carmen-main}/tools/migrate.py" prev

echo
echo "Preview environment is ready."
echo "  database: ${PREVIEW_DB_NAME} (${DB_ID})"
echo "Deploy and test the preview with: ./preview-test.sh"
echo "Remove everything with: ./preview-delete.sh"
