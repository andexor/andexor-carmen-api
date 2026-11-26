#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# Tries a real AI model on the preview, on demand. Never touches production.
#
# The pipeline's smoke test (preview-test.sh) uses only the stub model and makes no paid call.
# This script is the paid test: "run" calls the real provider and costs money. Run it when an
# adapter, a prompt, or a model has changed, not on every pull request.
#
# Usage:
#   ./ai-test.sh models                 List the models on the preview: id, provider, enabled, runnable.
#   ./ai-test.sh enable <model id>      Enable a model on the preview.
#   ./ai-test.sh disable <model id>     Disable a model on the preview, so that nobody runs it by mistake.
#   ./ai-test.sh run <model id> [website] [--research <model id>]
#                                       Research a company with the model, then score the dossier with
#                                       the same model. Prints the dossier, the proposed facts, the
#                                       scores, and what the run used. The test company stays on the
#                                       preview, and a later run with the same website uses it again
#                                       (specs/030-keep-preview-data/spec.md). The default website is
#                                       https://www.cloudflare.com. For a model that only scores,
#                                       such as Jev, name the model that researches with --research.
#
# Requires: curl, jq, and the Cloudflare Access service token in .env (see .env.example).

set -euo pipefail

cd "$(dirname "$0")"

if [[ -f .env ]]; then
    set -a
    . ./.env
    set +a
fi
HOST=$(jq -r '.env.preview.routes[0].pattern' wrangler.jsonc)
BASE="https://${HOST%%/*}/api/v1"

# call <curl arguments...>: prints the body; fails with the body when the status is 400 or above.
call() {
    local body status
    body=$(mktemp)
    status=$(curl -s -o "${body}" --write-out '%{http_code}' \
        -H "CF-Access-Client-Id: ${ACCESS_CLIENT_ID}" \
        -H "CF-Access-Client-Secret: ${ACCESS_CLIENT_SECRET}" \
        -H "Content-Type: application/json" "$@")
    if [[ "${status}" -ge 400 ]]; then
        echo "error: ${!#} returned ${status}: $(cat "${body}")" >&2
        rm -f "${body}"
        return 1
    fi
    cat "${body}"
    rm -f "${body}"
}

case "${1:-}" in
    models)
        call "${BASE}/ai/models" | jq -r '.items[] | [.id, .provider, .model_id, (if .active == 1 then "enabled" else "disabled" end), (if .runnable then "runnable" else "no adapter" end)] | @tsv' | column -t -s $'\t'
        ;;
    enable|disable)
        [[ -n "${2:-}" ]] || { echo "usage: $0 $1 <model id>" >&2; exit 1; }
        ACTIVE=$([[ "$1" == enable ]] && echo true || echo false)
        call -X PATCH -d "{\"active\":${ACTIVE}}" "${BASE}/ai/models/$2" | jq -r '"\(.label): \(if .active == 1 then "enabled" else "disabled" end)"'
        ;;
    run)
        MODEL=${2:-}
        [[ -n "${MODEL}" ]] || { echo "usage: $0 run <model id> [website] [--research <model id>]" >&2; exit 1; }
        WEBSITE=https://www.cloudflare.com
        RESEARCHER=${MODEL}
        shift 2
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --keep) ;;  # accepted for old habits: the company is always kept
                --research) RESEARCHER=${2:?--research needs a model id}; shift ;;
                *) WEBSITE=$1 ;;
            esac
            shift
        done

        echo "This calls a real model on the preview and costs money."
        # Two companies cannot share a website, and nothing is deleted on the preview, so the company
        # of an earlier run with this website is used again.
        DOMAIN=$(sed -E 's#^https?://##; s#^www\.##; s#/.*##' <<< "${WEBSITE,,}")
        COMPANY=$(call "${BASE}/companies?q=${DOMAIN}" | jq -r --arg d "${DOMAIN}" '[.items[] | select(.domain == $d)][0].id // empty')
        if [[ -z "${COMPANY}" ]]; then
            COMPANY=$(call -X POST -d "{\"website\":\"${WEBSITE}\",\"name\":\"AI test $(date -u +%Y%m%dT%H%M%SZ)\"}" "${BASE}/companies" | jq -r '.id')
        fi
        echo "The test company is ${COMPANY}. It stays on the preview."

        IDS=$(call -X POST -d "{\"research_model_id\":\"${RESEARCHER}\",\"scoring_model_ids\":[\"${MODEL}\"]}" "${BASE}/companies/${COMPANY}/runs/research-and-score")
        RESEARCH=$(jq -r '.run_ids[0]' <<< "${IDS}")
        SCORING=$(jq -r '.run_ids[1]' <<< "${IDS}")
        echo "Research run ${RESEARCH}, then scoring run ${SCORING}. Waiting (up to 10 minutes)..."
        for _ in $(seq 1 120); do
            STATUS=$(call "${BASE}/runs/${SCORING}" | jq -r '.status')
            [[ "${STATUS}" == "SUCCEEDED" || "${STATUS}" == "FAILED" ]] && break
            sleep 5
        done

        USED='{status, error, model_id_used, duration_ms, model_ms, input_tokens, output_tokens, search_count}'
        echo
        echo "== Research =="
        RUN=$(call "${BASE}/runs/${RESEARCH}")
        jq -c "${USED}" <<< "${RUN}"
        echo "-- Dossier --"
        jq -r '.dossier // "(none)"' <<< "${RUN}"
        echo "-- Proposed facts --"
        jq -r '.facts[] | "\(.key) = \(.value)   \(.source_url // "")"' <<< "${RUN}"
        echo
        echo "== Scoring =="
        RUN=$(call "${BASE}/runs/${SCORING}")
        jq -c "${USED} + {fit_score, need_score, confidence, recommendation, answers: (.answers | length)}" <<< "${RUN}"
        jq -r '.answers[] | "\(.value)\t\(.probability // "-")\t\(.prompt)\n\t\t  \(.rationale // "")"' <<< "${RUN}"
        echo
        [[ "${STATUS}" == "SUCCEEDED" ]] || { echo "The runs did not succeed: ${STATUS}"; exit 1; }
        ;;
    *)
        sed -n '/^# Usage/,/^# Requires/p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac
