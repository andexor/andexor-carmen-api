#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# Decommissions the preview environment made by preview-create.sh and preview-test.sh:
#
#   1. Deletes the preview Worker named in env.preview of wrangler.jsonc, and with it its route.
#   2. Deletes the preview D1 database, including all of its data.
#   4. Deletes the preview queue for AI runs.
#   3. Deletes the preview R2 bucket for documents, if it is empty. Cloudflare refuses to delete a
#      bucket that holds objects, so delete the documents first if the script says it could not.
#
# It never touches the production Worker, the production database, the production bucket, or the
# production route.
# The DNS record, the Cloudflare Access application, and the service token stay.
# Safe to run again: steps whose target is already gone are skipped.
# wrangler.jsonc keeps its env.preview block. preview-create.sh writes the new database id into it.
#
# Usage: ./preview-delete.sh [--yes]
#
#   --yes  Do not ask for confirmation.
#
# Requires: cf (logged in), jq, uv. wrangler.jsonc must be plain JSON, with no comments.

set -euo pipefail

cd "$(dirname "$0")"

WRANGLER_FILE=wrangler.jsonc
ASSUME_YES=false

case "${1:-}" in
    "") ;;
    --yes) ASSUME_YES=true ;;
    -h|--help) sed -n '/^# Usage/,/^# Requires/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "error: unknown argument $1" >&2; exit 1 ;;
esac

for tool in cf jq uv; do
    command -v "${tool}" > /dev/null || { echo "error: ${tool} is not installed" >&2; exit 1; }
done

jq -e . "${WRANGLER_FILE}" > /dev/null 2>&1 || {
    echo "error: ${WRANGLER_FILE} is not plain JSON (comments are not supported by this script)" >&2
    exit 1
}

PROD_WORKER_NAME=$(jq -r '.name' "${WRANGLER_FILE}")
PROD_DB_NAME=$(jq -r '.d1_databases[0].database_name' "${WRANGLER_FILE}")
PROD_DB_ID=$(jq -r '.d1_databases[0].database_id' "${WRANGLER_FILE}")
PREVIEW_WORKER_NAME=$(jq -r '.env.preview.name // empty' "${WRANGLER_FILE}")
PREVIEW_DB_NAME=$(jq -r '.env.preview.d1_databases[0].database_name // empty' "${WRANGLER_FILE}")
PROD_BUCKET=$(jq -r '.r2_buckets[0].bucket_name // empty' "${WRANGLER_FILE}")
PREVIEW_BUCKET=$(jq -r '.env.preview.r2_buckets[0].bucket_name // empty' "${WRANGLER_FILE}")

if [[ -z "${PREVIEW_WORKER_NAME}" || -z "${PREVIEW_DB_NAME}" ]]; then
    echo "error: env.preview in ${WRANGLER_FILE} needs a name and a d1_databases entry" >&2
    exit 1
fi

# Hard stop: never delete anything that carries a production name.
if [[ "${PREVIEW_WORKER_NAME}" == "${PROD_WORKER_NAME}" || "${PREVIEW_DB_NAME}" == "${PROD_DB_NAME}" ]]; then
    echo "error: env.preview uses a production name, refusing to delete anything" >&2
    exit 1
fi
if [[ -n "${PREVIEW_BUCKET}" && "${PREVIEW_BUCKET}" == "${PROD_BUCKET}" ]]; then
    echo "error: env.preview uses the production bucket, refusing to delete anything" >&2
    exit 1
fi

# Find what exists, so the confirmation can say exactly what will go.
WORKER_FOUND=$(cf workers list 2> /dev/null \
    | jq -r --arg n "${PREVIEW_WORKER_NAME}" '[.[] | select(.name == $n)][0].name // empty')
DB_ID=$(cf d1 list --name "${PREVIEW_DB_NAME}" 2> /dev/null \
    | jq -r --arg n "${PREVIEW_DB_NAME}" '[.[] | select(.name == $n)][0].uuid // empty')

BUCKET_FOUND=""
if [[ -n "${PREVIEW_BUCKET}" ]]; then
    BUCKET_FOUND=$(cf r2 buckets list 2> /dev/null | sed -n '/^{/,$p' \
        | jq -r --arg n "${PREVIEW_BUCKET}" '[.buckets[] | select(.name == $n)][0].name // empty')
fi

# Hard stop: never delete the production database.
if [[ -n "${DB_ID}" && "${DB_ID}" == "${PROD_DB_ID}" ]]; then
    echo "error: refusing to delete ${PREVIEW_DB_NAME}, its id matches the production database" >&2
    exit 1
fi

echo "This will delete:"
echo "  Worker ${PREVIEW_WORKER_NAME}: ${WORKER_FOUND:-not found}"
echo "  D1 database ${PREVIEW_DB_NAME}: ${DB_ID:-not found}, including all its data"
echo "  R2 bucket ${PREVIEW_BUCKET:-none configured}: ${BUCKET_FOUND:-not found}, only if it is empty"
echo "Production is not touched."

if [[ "${ASSUME_YES}" != true ]]; then
    read -r -p "Type yes to continue: " answer
    [[ "${answer}" == "yes" ]] || { echo "Cancelled."; exit 1; }
fi

# 1. The Worker first, because it is bound to the database.
if [[ -n "${WORKER_FOUND}" ]]; then
    echo "Deleting Worker ${PREVIEW_WORKER_NAME}"
    uv run pywrangler delete --env preview --force < /dev/null > /dev/null 2>&1 \
        || echo "  warning: could not delete ${PREVIEW_WORKER_NAME}"
fi

# 2. The preview database.
if [[ -n "${DB_ID}" ]]; then
    echo "Deleting D1 database ${PREVIEW_DB_NAME}"
    cf d1 delete "${DB_ID}" --force > /dev/null 2>&1 \
        || echo "  warning: could not delete ${PREVIEW_DB_NAME}"
fi

# 3. The preview bucket. Cloudflare deletes it only when it holds no objects.
if [[ -n "${BUCKET_FOUND}" ]]; then
    echo "Deleting R2 bucket ${PREVIEW_BUCKET}"
    cf r2 buckets delete "${PREVIEW_BUCKET}" --force > /dev/null 2>&1 \
        || echo "  warning: could not delete ${PREVIEW_BUCKET}; it must be empty first"
fi

# 4. The preview queue, after the Worker that consumed it.
PROD_QUEUE=$(jq -r '.queues.producers[0].queue // empty' "${WRANGLER_FILE}")
PREVIEW_QUEUE=$(jq -r '.env.preview.queues.producers[0].queue // empty' "${WRANGLER_FILE}")
if [[ -n "${PREVIEW_QUEUE}" && "${PREVIEW_QUEUE}" != "${PROD_QUEUE}" ]]; then
    QUEUE_ID=$(cf queues list 2> /dev/null | sed -n '/^\[/,$p' \
        | jq -r --arg n "${PREVIEW_QUEUE}" '[.[] | select(.queue_name == $n)][0].queue_id // empty')
    if [[ -n "${QUEUE_ID}" ]]; then
        echo "Deleting queue ${PREVIEW_QUEUE}"
        cf queues delete "${QUEUE_ID}" --force > /dev/null 2>&1 \
            || echo "  warning: could not delete ${PREVIEW_QUEUE}"
    fi
fi

echo "Preview environment removed."
