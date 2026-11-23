#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

# Runs the unit tests, migrates the preview database, deploys the preview Worker, and smoke tests
# it through Cloudflare Access. Stops at the first failure. Never touches production.
#
# The migrations live in andexor-carmen-main. Set CARMEN_MAIN to its checkout if it is not beside
# this repo.
#
# Usage: ./preview-test.sh
#
# Requires: uv, cf (logged in), python3, and what health.sh requires.

set -euo pipefail

cd "$(dirname "$0")"

echo "Unit tests..."
./unit-test.sh

echo "Applying migrations to the preview database..."
python3 "${CARMEN_MAIN:-../andexor-carmen-main}/tools/migrate.py" prev

echo "Deploying the preview..."
uv run pywrangler deploy --env preview

echo "Health check..."
./health.sh preview 10

# Smoke test. Each feature adds its calls here.
if [[ -f .env ]]; then
    set -a
    . ./.env
    set +a
fi
HOST=$(jq -r '.env.preview.routes[0].pattern' wrangler.jsonc)
BASE="https://${HOST%%/*}/api/v1"
BODY=$(mktemp)
HEAD=$(mktemp)
FILE=$(mktemp)
trap 'rm -f "${BODY}" "${HEAD}" "${FILE}"' EXIT

# expect <status> <text the body must contain, or '' for any body> <curl arguments, the URL last>
expect() {
    local want=$1 text=$2 status
    shift 2
    status=$(curl -s -o "${BODY}" -D "${HEAD}" --write-out '%{http_code}' \
        -H "CF-Access-Client-Id: ${ACCESS_CLIENT_ID}" \
        -H "CF-Access-Client-Secret: ${ACCESS_CLIENT_SECRET}" "$@")
    if [[ "${status}" != "${want}" ]] || { [[ -n "${text}" ]] && ! grep -q "${text}" "${BODY}"; }; then
        echo "FAILED: curl $* returned ${status}, expected ${want} with ${text}"
        cat "${BODY}"
        echo
        exit 1
    fi
    echo "ok: ${status} ${!#}"
}

echo "Smoke test..."
# The service token is a machine caller: ADMIN on the preview, and it has no app_user row.
expect 200 '"role":"ADMIN"' "${BASE}/me"
expect 200 '"machine":true' "${BASE}/me"
expect 404 '"error":"NOT_FOUND"' "${BASE}/no-such-path"

# Companies and contacts (specs/002-companies-and-contacts/spec.md). The test company is deleted at
# the end, and at the start if an earlier run failed and left it behind.
JSON=(-H "Content-Type: application/json")
expect 200 '"domain":"COMPANY_STAGE"' "${BASE}/srt"
expect 200 '"items"' "${BASE}/companies?q=smoke-test.invalid"
# A company with agreements or documents cannot be deleted, so those go first. An agreement must
# be a draft to be deleted.
for id in $(jq -r '.items[].id' "${BODY}"); do
    expect 200 '"items"' "${BASE}/companies/${id}/documents"
    for document in $(jq -r '.items[].id' "${BODY}"); do
        expect 204 '' -X DELETE "${BASE}/documents/${document}"
    done
    expect 200 '"items"' "${BASE}/agreements?company_id=${id}"
    for agreement in $(jq -r '.items[].id' "${BODY}"); do
        expect 200 '"status":"DRAFT"' -X PATCH "${JSON[@]}" -d '{"status":"DRAFT"}' "${BASE}/agreements/${agreement}"
        expect 204 '' -X DELETE "${BASE}/agreements/${agreement}"
    done
    expect 204 '' -X DELETE "${BASE}/companies/${id}"
done
expect 201 '"domain":"smoke-test.invalid"' -X POST "${JSON[@]}" \
    -d '{"name":"Smoke Test","website":"https://www.Smoke-Test.invalid/about"}' "${BASE}/companies"
ID=$(jq -r '.id' "${BODY}")
# A second company with the same domain is refused, and the answer names the first.
expect 409 "\"company_id\":\"${ID}\"" -X POST "${JSON[@]}" -d '{"website":"smoke-test.invalid"}' "${BASE}/companies"
# Deals (specs/007-deals/spec.md). The first deal moves the lead to PROSPECT by itself.
expect 201 '"name":"Smoke Test deal"' -X POST "${JSON[@]}" \
    -d "{\"company_id\":\"${ID}\",\"service_ids\":[\"web-development\"]}" "${BASE}/deals"
DEAL=$(jq -r '.id' "${BODY}")
expect 200 '"stage":"PROSPECT"' "${BASE}/companies/${ID}"
# A stage can always be set by hand.
expect 200 '"stage":"CUSTOMER"' -X PATCH "${JSON[@]}" -d '{"stage":"CUSTOMER"}' "${BASE}/companies/${ID}"
expect 400 '"error":"BAD_REQUEST"' -X PATCH "${JSON[@]}" -d '{"stage":"NO_SUCH_STAGE"}' "${BASE}/companies/${ID}"
# The database enforces foreign keys with no PRAGMA: an owner that does not exist is refused.
expect 400 '"error":"BAD_REQUEST"' -X PATCH "${JSON[@]}" -d '{"owner_id":"no-such-user"}' "${BASE}/companies/${ID}"
expect 201 '"is_primary":1' -X POST "${JSON[@]}" -d '{"name":"Smoke Contact","is_primary":true}' "${BASE}/companies/${ID}/contacts"
expect 200 '"name":"Smoke Contact"' "${BASE}/companies/${ID}"
expect 200 '"next_cursor":null' "${BASE}/companies?q=smoke+contact"

# The timeline and tasks (specs/003-activities-and-tasks/spec.md). Both hang off the test company,
# so deleting it below removes them.
expect 201 '"kind":"NOTE"' -X POST "${JSON[@]}" -d '{"body":"Smoke note"}' "${BASE}/companies/${ID}/activities"
expect 400 '"error":"BAD_REQUEST"' -X POST "${JSON[@]}" -d '{"kind":"SYSTEM"}' "${BASE}/companies/${ID}/activities"
# The stage change above wrote an automatic entry, and it is permanent.
expect 200 '"kind":"STAGE_CHANGE"' "${BASE}/companies/${ID}/activities?kind=STAGE_CHANGE"
ENTRY=$(jq -r '.items[0].id' "${BODY}")
expect 409 '"error":"CONFLICT"' -X PATCH "${JSON[@]}" -d '{"body":"edited"}' "${BASE}/activities/${ENTRY}"
expect 409 '"error":"CONFLICT"' -X DELETE "${BASE}/activities/${ENTRY}"
# A date in another form is stored in ISO 8601 form.
expect 201 '"due_date":"2026-12-01"' -X POST "${JSON[@]}" \
    -d "{\"title\":\"Smoke task\",\"company_id\":\"${ID}\",\"due_date\":\"12/1/2026\"}" "${BASE}/tasks"
TASK=$(jq -r '.id' "${BODY}")
expect 400 '"error":"BAD_REQUEST"' -X PATCH "${JSON[@]}" -d '{"due_date":"13/45/2026"}' "${BASE}/tasks/${TASK}"
# A fuzzy date is translated. This also proves that the date library runs in the Worker.
expect 200 "\"due_date\":\"$(date -u -d tomorrow +%F)\"" -X PATCH "${JSON[@]}" -d '{"due_date":"tomorrow"}' "${BASE}/tasks/${TASK}"
expect 200 '"title":"Smoke task"' "${BASE}/companies/${ID}"
expect 200 '"completed_at":"2' -X PATCH "${JSON[@]}" -d '{"status":"DONE"}' "${BASE}/tasks/${TASK}"
expect 200 '"title":"Smoke task"' "${BASE}/tasks?status=DONE&company_id=${ID}"

# Services, the rubric, and scores (specs/004-services-rubric-and-scoring/spec.md). The catalog and
# the rubric cannot be deleted, so nothing is added to them here; only the test company is answered.
expect 200 '"offered":' "${BASE}/services"
expect 200 '"section":"FIT"' "${BASE}/rubric"
CRITERION=$(jq -r '[.items[] | select(.active == 1 and .section == "FIT" and .answer_type == "YES_NO")][0].id' "${BODY}")
answer() { echo "{\"answers\":[{\"criterion_id\":\"${CRITERION}\",\"value\":$1}]}"; }
expect 200 '"fit_score":[0-9]' -X PUT "${JSON[@]}" -d "$(answer '"YES"')" "${BASE}/companies/${ID}/answers"
expect 200 '"value":"YES"' "${BASE}/companies/${ID}/answers"
expect 200 '"recommendation":"' "${BASE}/companies/${ID}"
expect 200 '"kind":"SCORE_CHANGE"' "${BASE}/companies/${ID}/activities?kind=SCORE_CHANGE"
expect 400 '"error":"BAD_REQUEST"' -X PUT "${JSON[@]}" -d "$(answer '"PERHAPS"')" "${BASE}/companies/${ID}/answers"
expect 200 '"fit_score":null' -X PUT "${JSON[@]}" -d "$(answer null)" "${BASE}/companies/${ID}/answers"
expect 400 '"error":"BAD_REQUEST"' -X POST "${JSON[@]}" -d '{"prompt":"Smoke","config_json":"{not json"}' "${BASE}/rubric"
expect 200 '"companies":' -X POST "${BASE}/rubric/recompute"

# Enrichment facts (specs/005-enrichment-facts/spec.md). They go with the test company.
expect 201 '"key":"smoke_fact"' -X POST "${JSON[@]}" -d '{"key":"Smoke Fact","value":"old","observed_at":"2026-01-01"}' "${BASE}/companies/${ID}/facts"
FACT=$(jq -r '.items[0].id' "${BODY}")
expect 201 '"source":"MANUAL"' -X POST "${JSON[@]}" \
    -d '{"facts":[{"key":"smoke_fact","value":"new"},{"key":"smoke_count","value":12}]}' "${BASE}/companies/${ID}/facts"
expect 400 '"error":"BAD_REQUEST"' -X POST "${JSON[@]}" -d '{"facts":[{"key":"smoke_fact","value":"x"},{"key":"smoke_fact"}]}' "${BASE}/companies/${ID}/facts"
expect 200 '"value":"old"' "${BASE}/companies/${ID}/facts"
expect 200 '"value":"new"' "${BASE}/companies/${ID}/facts?latest=true"
if grep -q '"value":"old"' "${BODY}"; then
    echo "FAILED: latest=true returned an older fact of the same key"
    exit 1
fi
expect 200 '"key":"smoke_count"' "${BASE}/facts/keys"
# An edit and a delete are each recorded on the timeline, with the old value, in the same transaction.
expect 200 '"value":"older"' -X PATCH "${JSON[@]}" -d '{"value":"older"}' "${BASE}/facts/${FACT}"
expect 200 "Fact smoke_fact edited: value 'old' to 'older'" "${BASE}/companies/${ID}/activities?kind=SYSTEM"
expect 204 '' -X DELETE "${BASE}/facts/${FACT}"
expect 200 "Fact smoke_fact deleted. It held: value 'older'" "${BASE}/companies/${ID}/activities?kind=SYSTEM"
expect 404 '"error":"NOT_FOUND"' -X DELETE "${BASE}/facts/${FACT}"

# Deals again: closing one, and the records that an edit and a delete leave on the timeline.
expect 200 '"closed_at":"2' -X PATCH "${JSON[@]}" -d '{"stage":"WON","monthly_value_cents":50000}' "${BASE}/deals/${DEAL}"
expect 200 'Deal Smoke Test deal: stage changed from QUALIFYING to WON' "${BASE}/companies/${ID}/activities?kind=STAGE_CHANGE"
expect 200 'Deal Smoke Test deal edited: monthly_value_cents None to 50000' "${BASE}/companies/${ID}/activities?kind=SYSTEM"
expect 200 '"items":\[\]' "${BASE}/deals?company_id=${ID}&open=true"
expect 200 '"service_ids":\["web-development"\]' "${BASE}/deals/${DEAL}"
expect 204 '' -X DELETE "${BASE}/deals/${DEAL}"
expect 200 'Deal Smoke Test deal deleted' "${BASE}/companies/${ID}/activities?kind=SYSTEM"

# The change log (specs/018-change-log/spec.md): an edit of any record leaves the old value on the timeline.
expect 200 '"city":"Atlanta"' -X PATCH "${JSON[@]}" -d '{"city":"Atlanta"}' "${BASE}/companies/${ID}"
expect 200 "Company Smoke Test edited: city None to 'Atlanta'" "${BASE}/companies/${ID}/activities?kind=SYSTEM"
expect 200 '"title":"Smoke task 2"' -X PATCH "${JSON[@]}" -d '{"title":"Smoke task 2"}' "${BASE}/tasks/${TASK}"
expect 200 "Task Smoke task edited: title 'Smoke task' to 'Smoke task 2'" "${BASE}/companies/${ID}/activities?kind=SYSTEM&limit=200"

# Agreements and documents (specs/008-agreements-and-documents/spec.md). The company is a CUSTOMER
# by hand at this point. Both are removed again here, because they block deleting the company.
expect 201 '"status":"DRAFT"' -X POST "${JSON[@]}" \
    -d "{\"company_id\":\"${ID}\",\"type\":\"MSA\",\"title\":\"Smoke MSA\",\"signed_date\":\"11/01/2026\"}" "${BASE}/agreements"
AGREEMENT=$(jq -r '.id' "${BODY}")
expect 200 '"status":"ACTIVE"' -X PATCH "${JSON[@]}" -d '{"status":"ACTIVE"}' "${BASE}/agreements/${AGREEMENT}"
expect 200 '"title":"Smoke MSA"' "${BASE}/agreements?company_id=${ID}&status=ACTIVE"
printf 'smoke document\n' > "${FILE}"
expect 201 '"size_bytes":15' -X POST -H "Content-Type: text/plain" --data-binary "@${FILE}" \
    "${BASE}/companies/${ID}/documents?file_name=smoke.txt&agreement_id=${AGREEMENT}"
DOCUMENT=$(jq -r '.id' "${BODY}")
if [[ "$(jq -r '.sha256' "${BODY}")" != "$(sha256sum < "${FILE}" | cut -d' ' -f1)" ]]; then
    echo "FAILED: the stored checksum is not the file's checksum"
    exit 1
fi
expect 200 'smoke document' "${BASE}/documents/${DOCUMENT}/content"
if ! grep -qi '^content-disposition: attachment; filename="smoke.txt"' "${HEAD}" || ! grep -qi '^content-type: text/plain' "${HEAD}"; then
    echo "FAILED: the download does not carry the file's name and type"
    exit 1
fi
expect 200 '"file_name":"smoke.txt"' "${BASE}/agreements/${AGREEMENT}"
expect 409 '"error":"CONFLICT"' -X DELETE "${BASE}/companies/${ID}"
expect 409 '"error":"CONFLICT"' -X DELETE "${BASE}/agreements/${AGREEMENT}"
expect 204 '' -X DELETE "${BASE}/documents/${DOCUMENT}"
expect 404 '"error":"NOT_FOUND"' "${BASE}/documents/${DOCUMENT}/content"
# When the last active agreement ends, the customer becomes a former customer.
expect 200 '"status":"DRAFT"' -X PATCH "${JSON[@]}" -d '{"status":"DRAFT"}' "${BASE}/agreements/${AGREEMENT}"
expect 200 '"stage":"FORMER"' "${BASE}/companies/${ID}"
expect 204 '' -X DELETE "${BASE}/agreements/${AGREEMENT}"

# The Today view (specs/006-today/spec.md). The overdue task goes with the test company.
expect 201 '"due_date":"2020-01-01"' -X POST "${JSON[@]}" \
    -d "{\"title\":\"Smoke overdue\",\"company_id\":\"${ID}\",\"due_date\":\"2020-01-01\"}" "${BASE}/tasks"
expect 200 '"title":"Smoke overdue"' "${BASE}/today"
for name in tasks_overdue tasks_due leads_unscored top_leads deals_without_task agreements_renewing runs_in_progress; do
    jq -e --arg name "${name}" '.[$name] | type == "array"' "${BODY}" > /dev/null || { echo "FAILED: /today has no list ${name}"; exit 1; }
done
# The date is in the configured time zone, which proves the tzdata package works inside the Worker.
ZONE=$(jq -r '.time_zone' "${BODY}")
if [[ "${ZONE}" == "UTC" || "$(jq -r '.date' "${BODY}")" != "$(TZ=${ZONE} date +%F)" ]]; then
    echo "FAILED: /today date $(jq -r '.date' "${BODY}") is not today in ${ZONE}"
    exit 1
fi
echo "ok: today is $(jq -r '.date' "${BODY}") in ${ZONE}"

expect 204 '' -X DELETE "${BASE}/companies/${ID}"
expect 404 '"error":"NOT_FOUND"' "${BASE}/companies/${ID}"
expect 200 '"items":\[\]' "${BASE}/tasks?company_id=${ID}"

# Cross-Origin Resource Sharing (specs/017-cors/spec.md): only an origin in ALLOWED_ORIGINS is echoed.
# header <yes|no> <text>: the response headers of the last call must, or must not, contain the text.
header() {
    if grep -qi "$2" "${HEAD}"; then found=yes; else found=no; fi
    if [[ "${found}" != "$1" ]]; then
        echo "FAILED: response header '$2' present: ${found}, expected: $1"
        exit 1
    fi
    echo "ok: header $2: $1"
}
ORIGIN=$(jq -r '.env.preview.vars.ALLOWED_ORIGINS' wrangler.jsonc | cut -d, -f1)
expect 200 '"role":"ADMIN"' -H "Origin: ${ORIGIN}" "${BASE}/me"
header yes "^access-control-allow-origin: ${ORIGIN}"
header yes "^access-control-allow-credentials: true"
expect 200 '"role":"ADMIN"' -H "Origin: https://not-allowed.invalid" "${BASE}/me"
header no "^access-control-allow-origin"
# A preflight (OPTIONS) is not checked here: Cloudflare Access answers it with 403 before the API
# sees it, even with the service token, until the setting in MANUAL-TASKS.md is turned on.
