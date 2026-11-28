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
# Every call and its answer, and the specification the Worker serves: compared at the end.
CALLS=$(mktemp)
SPEC=$(mktemp)
trap 'rm -f "${BODY}" "${HEAD}" "${FILE}" "${CALLS}" "${SPEC}"' EXIT

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
    record "${status}" "$@"
    echo "ok: ${status} ${!#}"
}

# record <status> <curl arguments>: keep the call and its answer as one JSON line, for the contract check.
record() {
    local status=$1 method="" data="" url=${!#}
    shift
    while [[ $# -gt 1 ]]; do
        case "$1" in
            -X) method=$2; shift ;;
            -d) data=$2; shift ;;
            --data-binary) data="(file)"; shift ;;
        esac
        shift
    done
    [[ -n "${method}" ]] || method=$([[ -n "${data}" ]] && echo POST || echo GET)
    jq -cn --arg method "${method}" --arg url "${url}" --arg status "${status}" --arg data "${data}" --rawfile body "${BODY}" \
        '{method: $method, url: $url, status: ($status | tonumber), request: $data, response: $body}' >> "${CALLS}"
}

echo "Smoke test..."
# The service token is a machine caller: ADMIN on the preview, and it has no app_user row.
expect 200 '"role":"ADMIN"' "${BASE}/me"
expect 200 '"machine":true' "${BASE}/me"
expect 404 '"error":"NOT_FOUND"' "${BASE}/no-such-path"

# The contract (specs/031-openapi/spec.md): the generated OpenAPI specification and the Swagger UI,
# behind Cloudflare Access like every other path.
ROOT_URL="${BASE%/v1}"
expect 200 '"openapi":"3.1' "${ROOT_URL}/openapi.json"
cp "${BODY}" "${SPEC}"
if [[ "$(jq -r '[.info.title, .info.version, (.paths | has("/api/v1/companies")), (.paths | has("/api/openapi.json"))] | @csv' "${BODY}")" != "\"$(jq -r '.name' wrangler.jsonc)\",\"$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)\",true,false" ]]; then
    echo "FAILED: the specification should carry the app's name and version and list the app's routes: $(jq -c '.info' "${BODY}")"
    exit 1
fi
echo "ok: the specification carries the app's name and version and lists the routes"
expect 200 '"ok":true' "${BASE}/health"
expect 200 'swagger-ui' "${ROOT_URL}/docs"
expect 404 '"error":"NOT_FOUND"' "${ROOT_URL}/redoc"
# With no credentials, Cloudflare Access answers before the Worker does, and never with the document.
for path in openapi.json docs; do
    if ! bare=$(curl -s -o "${BODY}" --write-out '%{http_code}' "${ROOT_URL}/${path}"); then
        echo "FAILED: ${ROOT_URL}/${path} could not be reached"
        exit 1
    fi
    if [[ "${bare}" == "200" ]] || grep -q '"openapi"\|swagger-ui' "${BODY}"; then
        echo "FAILED: ${ROOT_URL}/${path} answered ${bare} to a request with no credentials"
        exit 1
    fi
    echo "ok: ${bare} ${ROOT_URL}/${path} with no credentials"
done

# No delete endpoint is called, here or anywhere below (specs/030-keep-preview-data/spec.md):
# every record a run makes stays on the preview, to be looked at. So each run has a tag of its own in its names and
# websites, and the data of earlier runs is never in its way.
RUN="${RANDOM}${RANDOM}"
# Companies and contacts (specs/002-companies-and-contacts/spec.md).
JSON=(-H "Content-Type: application/json")
expect 200 '"domain":"COMPANY_STAGE"' "${BASE}/srt"
expect 200 '"items":\[\]' "${BASE}/companies?q=smoke-${RUN}.invalid"
expect 201 "\"domain\":\"smoke-${RUN}.invalid\"" -X POST "${JSON[@]}" \
    -d "{\"name\":\"Smoke Test ${RUN}\",\"website\":\"https://www.Smoke-${RUN}.invalid/about\"}" "${BASE}/companies"
ID=$(jq -r '.id' "${BODY}")
# A second company with the same domain is refused, and the answer names the first.
expect 409 "\"company_id\":\"${ID}\"" -X POST "${JSON[@]}" -d "{\"website\":\"smoke-${RUN}.invalid\"}" "${BASE}/companies"
# Deals (specs/007-deals/spec.md). The first deal moves the lead to PROSPECT by itself.
expect 201 "\"name\":\"Smoke Test ${RUN} deal\"" -X POST "${JSON[@]}" \
    -d "{\"company_id\":\"${ID}\",\"service_ids\":[\"web-development\"]}" "${BASE}/deals"
DEAL=$(jq -r '.id' "${BODY}")
expect 200 '"stage":"PROSPECT"' "${BASE}/companies/${ID}"
# A stage can always be set by hand.
expect 200 '"stage":"CUSTOMER"' -X PATCH "${JSON[@]}" -d '{"stage":"CUSTOMER"}' "${BASE}/companies/${ID}"
expect 400 '"error":"BAD_REQUEST"' -X PATCH "${JSON[@]}" -d '{"stage":"NO_SUCH_STAGE"}' "${BASE}/companies/${ID}"
# The database enforces foreign keys with no PRAGMA: an owner that does not exist is refused.
expect 400 '"error":"BAD_REQUEST"' -X PATCH "${JSON[@]}" -d '{"owner_id":"no-such-user"}' "${BASE}/companies/${ID}"
expect 201 '"is_primary":1' -X POST "${JSON[@]}" -d "{\"name\":\"Smoke Contact ${RUN}\",\"is_primary\":true}" "${BASE}/companies/${ID}/contacts"
expect 200 '"title":"Tester"' -X PATCH "${JSON[@]}" -d '{"title":"Tester"}' "${BASE}/contacts/$(jq -r '.id' "${BODY}")"
expect 200 "\"name\":\"Smoke Contact ${RUN}\"" "${BASE}/companies/${ID}"
expect 200 '"next_cursor":null' "${BASE}/companies?q=smoke+contact+${RUN}"

# The timeline and tasks (specs/003-activities-and-tasks/spec.md). Both hang off the test company.
expect 201 '"kind":"NOTE"' -X POST "${JSON[@]}" -d '{"body":"Smoke note"}' "${BASE}/companies/${ID}/activities"
expect 200 '"body":"Smoke note, edited"' -X PATCH "${JSON[@]}" -d '{"body":"Smoke note, edited"}' "${BASE}/activities/$(jq -r '.id' "${BODY}")"
expect 400 '"error":"BAD_REQUEST"' -X POST "${JSON[@]}" -d '{"kind":"SYSTEM"}' "${BASE}/companies/${ID}/activities"
# The stage change above wrote an automatic entry, and it is permanent.
expect 200 '"kind":"STAGE_CHANGE"' "${BASE}/companies/${ID}/activities?kind=STAGE_CHANGE"
ENTRY=$(jq -r '.items[0].id' "${BODY}")
expect 409 '"error":"CONFLICT"' -X PATCH "${JSON[@]}" -d '{"body":"edited"}' "${BASE}/activities/${ENTRY}"
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
# the rubric are shared, so nothing is added to them here; only the test company is answered.
expect 200 '"offered":' "${BASE}/services"
expect 200 '"section":"FIT"' "${BASE}/rubric"
CRITERION=$(jq -r '[.items[] | select(.active == 1 and .section == "FIT" and .answer_type == "YES_NO")][0].id' "${BODY}")
SECOND=$(jq -r '[.items[] | select(.active == 1 and .section == "FIT" and .answer_type == "YES_NO")][1].id' "${BODY}")
answer() { echo "{\"answers\":[{\"criterion_id\":\"${CRITERION}\",\"value\":$1}]}"; }
expect 200 '"fit_score":[0-9]' -X PUT "${JSON[@]}" -d "$(answer '"YES"')" "${BASE}/companies/${ID}/answers"
expect 200 '"value":"YES"' "${BASE}/companies/${ID}/answers"
expect 200 '"recommendation":"' "${BASE}/companies/${ID}"
expect 200 '"kind":"SCORE_CHANGE"' "${BASE}/companies/${ID}/activities?kind=SCORE_CHANGE"
# Two saves at the same moment (specs/039-score-version-mark/spec.md). Each write of scores gives the
# company a new mark, and a save that meets a moved mark is done again, so both succeed. The scores
# must then follow from both answers: a full rescoring right after changes nothing for this company.
expect 200 '"score_token":"' "${BASE}/companies/${ID}"
MARK=$(jq -r '.score_token' "${BODY}")
save() {
    curl -s -o /dev/null --write-out '%{http_code}' -X PUT "${JSON[@]}" -H "CF-Access-Client-Id: ${ACCESS_CLIENT_ID}" \
        -H "CF-Access-Client-Secret: ${ACCESS_CLIENT_SECRET}" -d "$1" "${BASE}/companies/${ID}/answers"
}
save "$(answer '"NO"')" > "${FILE}.a" &
save "{\"answers\":[{\"criterion_id\":\"${SECOND}\",\"value\":\"YES\"}]}" > "${FILE}.b" &
wait
if [[ "$(cat "${FILE}.a" "${FILE}.b"; rm -f "${FILE}.a" "${FILE}.b")" != "200200" ]]; then
    echo "FAILED: two saves of answers at the same moment should both succeed"
    exit 1
fi
expect 200 '"score_token":"' "${BASE}/companies/${ID}"
SCORED=$(jq -c '[.fit_score, .need_score, .confidence, .recommendation]' "${BODY}")
if [[ "$(jq -r '.score_token' "${BODY}")" == "${MARK}" ]]; then
    echo "FAILED: a write of scores should give the company a new mark"
    exit 1
fi
expect 200 '"companies":' -X POST "${BASE}/rubric/recompute"
expect 200 '"score_token":"' "${BASE}/companies/${ID}"
if [[ "$(jq -c '[.fit_score, .need_score, .confidence, .recommendation]' "${BODY}")" != "${SCORED}" ]]; then
    echo "FAILED: after two saves at the same moment the scores do not follow from the answers: ${SCORED} became $(cat "${BODY}")"
    exit 1
fi
echo "ok: two saves at the same moment left scores that follow from both answers"
expect 200 '"fit_score":[0-9]' -X PUT "${JSON[@]}" -d "{\"answers\":[{\"criterion_id\":\"${SECOND}\",\"value\":null}]}" "${BASE}/companies/${ID}/answers"
expect 400 '"error":"BAD_REQUEST"' -X PUT "${JSON[@]}" -d "$(answer '"PERHAPS"')" "${BASE}/companies/${ID}/answers"
expect 200 '"fit_score":null' -X PUT "${JSON[@]}" -d "$(answer null)" "${BASE}/companies/${ID}/answers"
expect 400 '"error":"BAD_REQUEST"' -X POST "${JSON[@]}" -d '{"prompt":"Smoke","config_json":"{not json"}' "${BASE}/rubric"
expect 200 '"companies":' -X POST "${BASE}/rubric/recompute"
# A service id is in the form of a page name (specs/035-service-id-form/spec.md). A refusal adds nothing.
expect 400 '"error":"BAD_REQUEST"' -X POST "${JSON[@]}" -d '{"name":"Smoke","id":"smoke,test"}' "${BASE}/services"

# Enrichment facts (specs/005-enrichment-facts/spec.md).
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
# An edit is recorded on the timeline, with the old value, in the same transaction.
expect 200 '"value":"older"' -X PATCH "${JSON[@]}" -d '{"value":"older"}' "${BASE}/facts/${FACT}"
expect 200 "Fact smoke_fact edited: value 'old' to 'older'" "${BASE}/companies/${ID}/activities?kind=SYSTEM"

# Deals again: closing one, and the record that an edit leaves on the timeline.
expect 200 '"closed_at":"2' -X PATCH "${JSON[@]}" -d '{"stage":"WON","monthly_value_cents":50000}' "${BASE}/deals/${DEAL}"
expect 200 "Deal Smoke Test ${RUN} deal: stage changed from QUALIFYING to WON" "${BASE}/companies/${ID}/activities?kind=STAGE_CHANGE"
expect 200 "Deal Smoke Test ${RUN} deal edited: monthly_value_cents None to 50000" "${BASE}/companies/${ID}/activities?kind=SYSTEM"
expect 200 '"items":\[\]' "${BASE}/deals?company_id=${ID}&open=true"
expect 200 '"service_ids":\["web-development"\]' "${BASE}/deals/${DEAL}"

# The change log (specs/018-change-log/spec.md): an edit of any record leaves the old value on the timeline.
expect 200 '"city":"Atlanta"' -X PATCH "${JSON[@]}" -d '{"city":"Atlanta"}' "${BASE}/companies/${ID}"
expect 200 "Company Smoke Test ${RUN} edited: city None to 'Atlanta'" "${BASE}/companies/${ID}/activities?kind=SYSTEM"
expect 200 '"title":"Smoke task 2"' -X PATCH "${JSON[@]}" -d '{"title":"Smoke task 2"}' "${BASE}/tasks/${TASK}"
expect 200 "Task Smoke task edited: title 'Smoke task' to 'Smoke task 2'" "${BASE}/companies/${ID}/activities?kind=SYSTEM&limit=200"

# Agreements and documents (specs/008-agreements-and-documents/spec.md). The company is a CUSTOMER
# by hand at this point.
expect 201 '"status":"DRAFT"' -X POST "${JSON[@]}" \
    -d "{\"company_id\":\"${ID}\",\"type\":\"MSA\",\"title\":\"Smoke MSA\",\"signed_date\":\"11/01/2026\"}" "${BASE}/agreements"
AGREEMENT=$(jq -r '.id' "${BODY}")
# An agreement cannot become active without a renewal policy and an end basis (specs/024-renewal-policy/spec.md).
expect 400 '"error":"BAD_REQUEST"' -X PATCH "${JSON[@]}" -d '{"status":"ACTIVE"}' "${BASE}/agreements/${AGREEMENT}"
expect 400 '"error":"BAD_REQUEST"' -X PATCH "${JSON[@]}" -d '{"status":"ACTIVE","renewal_policy":"AUTO_RENEW"}' "${BASE}/agreements/${AGREEMENT}"
expect 400 '"error":"BAD_REQUEST"' -X PATCH "${JSON[@]}" \
    -d '{"status":"ACTIVE","renewal_policy":"AUTO_RENEW","end_basis":"INTERNAL_DECISION"}' "${BASE}/agreements/${AGREEMENT}"
expect 400 '"error":"BAD_REQUEST"' -X PATCH "${JSON[@]}" \
    -d '{"status":"ACTIVE","renewal_policy":"AUTO_RENEW","end_basis":"SIGNED_CONTRACT","end_date":"2030-01-31"}' "${BASE}/agreements/${AGREEMENT}"
expect 400 '"error":"BAD_REQUEST"' -X PATCH "${JSON[@]}" \
    -d '{"status":"ACTIVE","renewal_policy":"APPROVAL","end_basis":"SIGNED_CONTRACT","end_date":"2030-01-31"}' "${BASE}/agreements/${AGREEMENT}"
expect 200 '"status":"ACTIVE"' -X PATCH "${JSON[@]}" \
    -d '{"status":"ACTIVE","renewal_policy":"AUTO_RENEW","end_basis":"SIGNED_CONTRACT"}' "${BASE}/agreements/${AGREEMENT}"
# Only an agreement that renews on approval can have its renewal approved.
expect 409 '"error":"CONFLICT"' -X POST "${BASE}/agreements/${AGREEMENT}/renewal"
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
expect 200 '"file_name":"smoke.txt"' "${BASE}/documents/${DOCUMENT}"
expect 200 '"file_name":"smoke.txt"' "${BASE}/companies/${ID}/documents"
# When the last active agreement ends, the customer becomes a former customer.
expect 200 '"status":"DRAFT"' -X PATCH "${JSON[@]}" -d '{"status":"DRAFT"}' "${BASE}/agreements/${AGREEMENT}"
expect 200 '"stage":"FORMER"' "${BASE}/companies/${ID}"

# AI runs (specs/009-ai-runs/spec.md). The stub model is enabled on the preview only; it returns
# fixed results, so no outside service is called.
expect 200 '"provider":"STUB"' "${BASE}/ai/models?can_research=true"
STUB=$(jq -r '[.items[] | select(.provider == "STUB")][0].id' "${BODY}")
expect 200 '"active":1' -X PATCH "${JSON[@]}" -d '{"active":true}' "${BASE}/ai/models/${STUB}"
expect 202 '"run_ids"' -X POST "${JSON[@]}" \
    -d "{\"research_model_id\":\"${STUB}\",\"scoring_model_ids\":[\"${STUB}\"]}" "${BASE}/companies/${ID}/runs/research-and-score"
RESEARCH=$(jq -r '.run_ids[0]' "${BODY}")
SCORING=$(jq -r '.run_ids[1]' "${BODY}")
# The work is done from the queue, after the response. The scoring run waits for the research run.
for attempt in $(seq 1 60); do
    expect 200 '"kind":"SCORING"' "${BASE}/runs/${SCORING}" > /dev/null
    STATUS=$(jq -r '.status' "${BODY}")
    [[ "${STATUS}" == "SUCCEEDED" || "${STATUS}" == "FAILED" ]] && break
    sleep 2
done
echo "ok: the runs finished with ${STATUS} after ${attempt} checks"
expect 200 '"status":"SUCCEEDED"' "${BASE}/runs/${SCORING}"
expect 200 '"decision":"PROPOSED"' "${BASE}/runs/${RESEARCH}"
# Nothing a run produces reaches the company until someone decides.
expect 200 '"decision":"ACCEPTED"' -X POST "${JSON[@]}" -d '{"all":"ACCEPTED"}' "${BASE}/runs/${RESEARCH}/facts/decide"
expect 200 '"source":"AI"' "${BASE}/companies/${ID}/facts"
expect 409 '"error":"CONFLICT"' -X POST "${BASE}/runs/${RESEARCH}/apply"
expect 200 '"recommendation":"' -X POST "${BASE}/runs/${SCORING}/apply"
expect 200 '"source":"AI"' "${BASE}/companies/${ID}/answers"
expect 200 '"disagreement":false' "${BASE}/companies/${ID}/runs/compare?run_ids=${SCORING}"
expect 200 '"user_rating":5' -X PATCH "${JSON[@]}" -d '{"user_rating":5}' "${BASE}/runs/${SCORING}"
expect 200 '"agreement_rate":1' "${BASE}/ai/models/stats"
expect 200 '"kind":"RESEARCH"' "${BASE}/companies/${ID}/runs"

# A measured criterion is answered by code from its fact, whatever the model says
# (specs/021-measured-criteria/spec.md). "Is the site fast?" is YES at a PageSpeed score of 90 or more.
expect 201 '"key":"pagespeed_mobile"' -X POST "${JSON[@]}" -d '{"key":"pagespeed_mobile","value":95}' "${BASE}/companies/${ID}/facts"
expect 202 '"run_ids"' -X POST "${JSON[@]}" -d "{\"kind\":\"SCORING\",\"ai_model_ids\":[\"${STUB}\"]}" "${BASE}/companies/${ID}/runs"
MEASURED=$(jq -r '.run_ids[0]' "${BODY}")
for attempt in $(seq 1 60); do
    expect 200 '"kind":"SCORING"' "${BASE}/runs/${MEASURED}" > /dev/null
    STATUS=$(jq -r '.status' "${BODY}")
    [[ "${STATUS}" == "SUCCEEDED" || "${STATUS}" == "FAILED" ]] && break
    sleep 2
done
expect 200 'Measured, not judged: the fact pagespeed_mobile is 95, and 90 or more is YES.' "${BASE}/runs/${MEASURED}"
# The stub is switched off again, as it is in production: the preview's settings must not drift away
# from production's because a test ran.
expect 200 '"active":0' -X PATCH "${JSON[@]}" -d '{"active":false}' "${BASE}/ai/models/${STUB}"

# The Today view (specs/006-today/spec.md). The overdue task is marked done at the end, so the
# tasks of earlier runs do not fill the list.
expect 201 '"due_date":"2020-01-01"' -X POST "${JSON[@]}" \
    -d "{\"title\":\"Smoke overdue\",\"company_id\":\"${ID}\",\"due_date\":\"2020-01-01\"}" "${BASE}/tasks"
OVERDUE=$(jq -r '.id' "${BODY}")
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
expect 200 '"status":"DONE"' -X PATCH "${JSON[@]}" -d '{"status":"DONE"}' "${BASE}/tasks/${OVERDUE}"

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

# CSV import (specs/013-csv-import/spec.md): three rows, of which one repeats a website and one has no company.
TAG="imp${RANDOM}${RANDOM}"
CSV=$(printf 'Company,Website,First Name,Last Name,Email,Founded\nImport One,https://www.%s.example,Ann,Lee,ann@%s.example,1999\nImport Two,%s.example,,,,\n,,Bob,,,\n' "${TAG}" "${TAG}" "${TAG}")
expect 200 '"First Name":"contact.name"' -X POST -H 'Content-Type: application/json' \
    -d "$(jq -n --arg csv "${CSV}" '{csv: $csv}')" "${BASE}/imports/preview"
expect 400 '"error":"BAD_REQUEST"' -X POST -H 'Content-Type: application/json' \
    -d "$(jq -n --arg csv "${CSV}" '{csv: $csv, mapping: {Email: "contact.email"}}')" "${BASE}/imports"
MAPPING='{"Company":"company.name","Website":"company.website","First Name":"contact.name","Last Name":"contact.name","Email":"contact.email","Founded":"fact"}'
expect 202 '"id"' -X POST -H 'Content-Type: application/json' \
    -d "$(jq -n --arg csv "${CSV}" --argjson mapping "${MAPPING}" '{csv: $csv, file_name: "smoke.csv", mapping: $mapping}')" "${BASE}/imports"
IMPORT=$(jq -r '.id' "${BODY}")
for _ in $(seq 30); do
    expect 200 '"row_count":3' "${BASE}/imports/${IMPORT}" > /dev/null
    [[ "$(jq -r '.status' "${BODY}")" != "RUNNING" ]] && break
    sleep 2
done
expect 200 '"status":"DONE"' "${BASE}/imports/${IMPORT}"
if [[ "$(jq -c '[.created_count, .duplicate_count, .error_count, .duplicates[0].row, .duplicates[0].of_row, .errors[0].row]' "${BODY}")" != "[1,1,1,3,2,4]" ]]; then
    echo "FAILED: the import counted $(cat "${BODY}")"
    exit 1
fi
echo "ok: the import created 1 company, and reported row 3 as a duplicate and row 4 as an error"
expect 200 '"source":"CSV"' "${BASE}/companies?q=${TAG}"
IMPORTED=$(jq -r --arg d "${TAG}.example" '.items[] | select(.domain == $d) | .id' "${BODY}")
expect 200 '"name":"Ann Lee"' "${BASE}/companies/${IMPORTED}/contacts"
expect 200 '"key":"founded"' "${BASE}/companies/${IMPORTED}/facts"
expect 404 '"error":"NOT_FOUND"' "${BASE}/imports/00000000-0000-4000-8000-00000000dead"

# Machine methods (specs/014-machine-methods/spec.md), through the endpoint door. The Service Binding
# door runs the same code; no outside caller can reach it, so it is not called here.
JSON=(-X POST -H 'Content-Type: application/json')
MTAG="lead${RANDOM}${RANDOM}"
LEAD="{\"source\":\"CONTACT_US\",\"source_ref\":\"row-${MTAG}\",\"website\":\"https://www.${MTAG}.example\",\"contact\":{\"name\":\"Mia Lead\",\"email\":\"mia@${MTAG}.example\"},\"note\":\"Please call me\",\"facts\":[{\"key\":\"Has website\",\"value\":\"yes\"}]}"
expect 201 '"created":true' "${JSON[@]}" -d "${LEAD}" "${BASE}/machine/leads"
LEAD_ID=$(jq -r '.company_id' "${BODY}")
# The same lead again matches on the domain: no second company, and the same person is not added twice.
expect 200 "\"company_id\":\"${LEAD_ID}\",\"created\":false" "${JSON[@]}" -d "${LEAD}" "${BASE}/machine/leads"
# No website this time: it matches on the source and its reference.
expect 200 "\"company_id\":\"${LEAD_ID}\",\"created\":false" "${JSON[@]}" \
    -d "{\"source\":\"CONTACT_US\",\"source_ref\":\"row-${MTAG}\",\"contact\":{\"name\":\"Ned Other\"}}" "${BASE}/machine/leads"
expect 200 '"source":"CONTACT_US"' "${BASE}/companies/${LEAD_ID}"
expect 200 '"name":"Ned Other"' "${BASE}/companies/${LEAD_ID}/contacts"
if [[ "$(jq -c '[(.items | length), ([.items[] | select(.is_primary == 1)] | length)]' "${BODY}")" != "[2,1]" ]]; then
    echo "FAILED: the lead should have two contacts, one of them primary: $(cat "${BODY}")"
    exit 1
fi
echo "ok: the lead has two contacts, one of them primary"
expect 200 '"body":"Please call me"' "${BASE}/companies/${LEAD_ID}/activities"
expect 200 '"added":2' "${JSON[@]}" \
    -d "{\"domain\":\"${MTAG}.example\",\"source\":\"GRASS_SPIDER\",\"facts\":[{\"key\":\"registrar\",\"value\":\"Example Registrar\"},{\"key\":\"mx\",\"value\":\"none\"}]}" "${BASE}/machine/facts"
expect 200 '"source":"GRASS_SPIDER"' "${BASE}/companies/${LEAD_ID}/facts?key=registrar"
expect 200 '"source":"CONTACT_US"' "${BASE}/companies/${LEAD_ID}/facts?key=has_website"
expect 404 '"error":"NOT_FOUND"' "${JSON[@]}" -d '{"domain":"no-such-zz9.example","source":"API","facts":[{"key":"a","value":"b"}]}' "${BASE}/machine/facts"
expect 400 '"error":"BAD_REQUEST"' "${JSON[@]}" -d '{"name":"No source"}' "${BASE}/machine/leads"
expect 400 '"error":"BAD_REQUEST"' "${JSON[@]}" -d '{"source":"NO_SUCH_SOURCE","name":"Bad source"}' "${BASE}/machine/leads"
# A person and nothing else: the company is named after them.
expect 201 '"created":true' "${JSON[@]}" -d "{\"source\":\"CONTACT_US\",\"contact\":{\"name\":\"Solo ${MTAG}\"}}" "${BASE}/machine/leads"
SOLO_ID=$(jq -r '.company_id' "${BODY}")
expect 200 "\"name\":\"Solo ${MTAG}\"" "${BASE}/companies/${SOLO_ID}"
# Two businesses with pages on one shared site are two companies (specs/033-audit-fixes/spec.md).
expect 200 '"key":"company.shared_hosts"' "${BASE}/settings"
expect 201 '"created":true' "${JSON[@]}" -d "{\"source\":\"CONTACT_US\",\"website\":\"https://www.facebook.com/joes-${MTAG}\",\"contact\":{\"name\":\"Joe ${MTAG}\"}}" "${BASE}/machine/leads"
expect 201 '"created":true' "${JSON[@]}" -d "{\"source\":\"CONTACT_US\",\"website\":\"https://www.facebook.com/anns-${MTAG}\",\"contact\":{\"name\":\"Ann ${MTAG}\"}}" "${BASE}/machine/leads"
expect 200 '"domain":null' "${BASE}/companies/$(jq -r '.company_id' "${BODY}")"
# Braces in a name and a website that is not an address are stored as given. The scheduler runs
# further down still report no failed step: neither stops the digest or the uptime checks.
expect 201 '"created":true' "${JSON[@]}" -d "{\"source\":\"CONTACT_US\",\"name\":\"Braces {{x ${MTAG}\",\"website\":\"${MTAG}.example]\"}" "${BASE}/machine/leads"

# The scheduler and notifications (specs/015-scheduler-and-notifications/spec.md). The preview's
# provider is the stub, so no email is sent. The dates leave a margin of days, so the time zone
# of the machine that runs this does not matter.
STAG="sched${RANDOM}${RANDOM}"
expect 201 '"stage":"LEAD"' "${JSON[@]}" -d "{\"name\":\"Scheduler ${STAG}\"}" "${BASE}/companies"
SCO=$(jq -r '.id' "${BODY}")
expect 201 '"status":"ACTIVE"' "${JSON[@]}" \
    -d "{\"company_id\":\"${SCO}\",\"type\":\"MSA\",\"title\":\"Renewing ${STAG}\",\"status\":\"ACTIVE\",\"end_date\":\"$(date -u -d '+10 days' +%F)\",\"renewal_notice_days\":30,\"renewal_policy\":\"APPROVAL\",\"end_basis\":\"SIGNED_CONTRACT\",\"term_months\":6}" "${BASE}/agreements"
RENEWING=$(jq -r '.id' "${BODY}")
expect 201 '"status":"ACTIVE"' "${JSON[@]}" \
    -d "{\"company_id\":\"${SCO}\",\"type\":\"MSA\",\"title\":\"Ended ${STAG}\",\"status\":\"ACTIVE\",\"end_date\":\"$(date -u -d '-3 days' +%F)\",\"renewal_policy\":\"APPROVAL\",\"end_basis\":\"INTERNAL_DECISION\",\"end_basis_reason\":\"Smoke test\",\"term_months\":6}" "${BASE}/agreements"
ENDED=$(jq -r '.id' "${BODY}")
expect 201 '"status":"ACTIVE"' "${JSON[@]}" \
    -d "{\"company_id\":\"${SCO}\",\"type\":\"SOW\",\"title\":\"Project ${STAG}\",\"status\":\"ACTIVE\",\"end_date\":\"$(date -u -d '-3 days' +%F)\",\"renewal_policy\":\"ONE_TIME\",\"end_basis\":\"SIGNED_CONTRACT\"}" "${BASE}/agreements"
PROJECT=$(jq -r '.id' "${BODY}")
# The uptime checker (specs/026-uptime-checker/spec.md). A name under .example never resolves, so
# the check is down whatever the network does, and no real site is called.
expect 201 '"stage":"LEAD"' "${JSON[@]}" -d "{\"name\":\"Uptime ${STAG}\",\"website\":\"https://www.up-${STAG}.example\"}" "${BASE}/companies"
UPCO=$(jq -r '.id' "${BODY}")
expect 200 '"checks":0' "${BASE}/companies/${UPCO}/uptime"
expect 200 '"failed":\[\]' -X POST "${BASE}/scheduler/run"
if [[ "$(jq -c '[.expired >= 1, .completed >= 1, .alerts >= 1]' "${BODY}")" != "[true,true,true]" ]]; then
    echo "FAILED: the run should expire one agreement, complete another, and alert for a third: $(cat "${BODY}")"
    exit 1
fi
echo "ok: the run expired an agreement, completed another, and made an alert"
# The run checked the new lead's site once, and stored the share of successful checks as a fact.
expect 200 '"checks":1' "${BASE}/companies/${UPCO}/uptime"
if [[ "$(jq -c '[.percent == 0, .up, .measuring, .items[0].up, (.items[0].detail | length > 0)]' "${BODY}")" != "[true,0,true,0,true]" ]]; then
    echo "FAILED: one failed check, still measuring, was expected: $(cat "${BODY}")"
    exit 1
fi
expect 200 '"source":"UPTIME_CHECK"' "${BASE}/companies/${UPCO}/facts?key=uptime_percent"
# A company with no website cannot be measured, and an unknown company is not found.
expect 409 '"error":"CONFLICT"' -X POST "${BASE}/companies/${SCO}/uptime"
expect 404 '"error":"NOT_FOUND"' "${BASE}/companies/00000000-0000-4000-8000-00000000dead/uptime"
expect 200 '"status":"EXPIRED"' "${BASE}/agreements/${ENDED}"
expect 200 '"status":"COMPLETED"' "${BASE}/agreements/${PROJECT}"
# Service proposals (specs/025-service-proposals/spec.md). Completing the one-time agreement queued
# its job. On the preview the job uses the stub model and the stub provider: no model is paid and
# no email is sent. A second start soon after is refused (specs/036-proposals-once/spec.md), and
# says when the job was started and when it finished. A fresh job is then started by hand.
PROPOSALS="${BASE}/notifications?kind=SERVICE_PROPOSALS&agreement_id=${PROJECT}"
for wanted in 1 2; do
    for attempt in $(seq 1 60); do
        expect 200 '"items"' "${PROPOSALS}" > /dev/null
        [[ "$(jq -r '.items | length' "${BODY}")" -ge "${wanted}" ]] && break
        sleep 2
    done
    echo "ok: proposals email ${wanted} was stored after ${attempt} checks"
    expect 200 "\"subject\":\"PREV: Proposals for Scheduler ${STAG}\"" "${PROPOSALS}"
    if [[ "$(jq -c '[.items[] | (.status == "STUBBED" and (.body | contains("failed") | not) and (.body | contains("Research with")) and (.body | contains("Scoring with")))] | all' "${BODY}")" != "true" ]]; then
        echo "FAILED: a proposals email should be stubbed, list its runs, and say nothing of a failed run: $(cat "${BODY}")"
        exit 1
    fi
    if [[ "${wanted}" == "1" ]]; then
        expect 200 '"proposals_started_at":"2' "${BASE}/agreements/${PROJECT}"
        expect 409 '"finished_at":"2' -X POST "${BASE}/agreements/${PROJECT}/proposals"
        expect 202 '"queued":true' -X POST "${BASE}/agreements/${PROJECT}/proposals?fresh=true"
    fi
done
echo "ok: the proposals emails came from fresh stub runs"
expect 200 '"provider":"STUB"' "${BASE}/companies/${SCO}/runs"
expect 200 '"scored_at":"2' "${BASE}/companies/${SCO}/proposals"
expect 404 '"error":"NOT_FOUND"' "${BASE}/companies/00000000-0000-4000-8000-00000000dead/proposals"
expect 404 '"error":"NOT_FOUND"' -X POST "${BASE}/agreements/00000000-0000-4000-8000-00000000dead/proposals"
expect 409 '"error":"CONFLICT"' -X POST "${BASE}/agreements/${ENDED}/renewal"
expect 200 "It ended on" "${BASE}/companies/${SCO}/activities?kind=SYSTEM"
expect 200 "\"subject\":\"PREV: Renewal: Renewing ${STAG} ends" "${BASE}/notifications?kind=RENEWAL_ALERT&agreement_id=${RENEWING}"
expect 200 '"status":"STUBBED"' "${BASE}/notifications?kind=RENEWAL_ALERT&agreement_id=${RENEWING}"
expect 200 "\"title\":\"Renewal: Renewing ${STAG}\"" "${BASE}/tasks?company_id=${SCO}"
# A second run makes no second alert and no second task.
expect 200 '"failed":\[\]' -X POST "${BASE}/scheduler/run"
expect 200 '"next_cursor":null' "${BASE}/notifications?kind=RENEWAL_ALERT&agreement_id=${RENEWING}"
if [[ "$(jq -c '.items | length' "${BODY}")" != "1" ]]; then
    echo "FAILED: one alert was expected: $(cat "${BODY}")"
    exit 1
fi
echo "ok: the second run made no second alert"
# The second run made no second uptime check: a site is checked once a day.
expect 200 '"checks":1' "${BASE}/companies/${UPCO}/uptime"
# A person starts a measurement. It is started on a new company with no checks yet, so nothing is replaced.
expect 201 '"stage":"LEAD"' "${JSON[@]}" -d "{\"name\":\"Uptime by hand ${STAG}\",\"website\":\"https://www.hand-${STAG}.example\"}" "${BASE}/companies"
expect 200 '"checks":1' -X POST "${BASE}/companies/$(jq -r '.id' "${BODY}")/uptime"
# Approving the renewal moves the end date forward by the term, records it, and closes the task.
expect 200 '"status":"ACTIVE"' -X POST "${BASE}/agreements/${RENEWING}/renewal"
if [[ "$(jq -r '.end_date' "${BODY}")" < "$(date -u -d '+5 months' +%F)" ]]; then
    echo "FAILED: the end date should be about six months later: $(cat "${BODY}")"
    exit 1
fi
echo "ok: the renewal moved the end date forward by the term"
expect 200 "renewal approved for 6 months" "${BASE}/companies/${SCO}/activities?kind=SYSTEM"
expect 200 '"status":"DONE"' "${BASE}/tasks?company_id=${SCO}"

# Settings (specs/016-settings/spec.md). A change is put back, so the preview keeps its values.
expect 200 '"key":"digest.hour_local"' "${BASE}/settings"
ROWS=$(jq -r '.items[] | select(.key == "import.max_rows") | .value' "${BODY}")
PATCH=(-X PATCH -H 'Content-Type: application/json')
expect 200 "\"value\":\"$((ROWS + 1))\"" "${PATCH[@]}" -d "{\"import.max_rows\":$((ROWS + 1))}" "${BASE}/settings"
expect 200 "\"value\":\"${ROWS}\"" "${PATCH[@]}" -d "{\"import.max_rows\":\"${ROWS}\"}" "${BASE}/settings"
expect 400 '"error":"BAD_REQUEST"' "${PATCH[@]}" -d '{"no.such.setting":"1"}' "${BASE}/settings"
expect 400 '"error":"BAD_REQUEST"' "${PATCH[@]}" -d '{"digest.hour_local":"25"}' "${BASE}/settings"
expect 400 '"error":"BAD_REQUEST"' "${PATCH[@]}" -d '{"digest.time_zone":"Mars/Olympus_Mons"}' "${BASE}/settings"
expect 400 '"error":"BAD_REQUEST"' "${PATCH[@]}" -d '{"digest.recipient":"Some One <not an address>"}' "${BASE}/settings"
expect 400 '"error":"BAD_REQUEST"' "${PATCH[@]}" -d '{"ai.default_scoring_model":"no-such-model"}' "${BASE}/settings"
# One bad value refuses the whole change: the good one beside it is not stored.
expect 400 '"error":"BAD_REQUEST"' "${PATCH[@]}" -d "{\"import.max_rows\":$((ROWS + 7)),\"score.min_confidence\":101}" "${BASE}/settings"
expect 200 "\"value\":\"${ROWS}\"" "${BASE}/settings"

# A rescoring that fails is made again (specs/034-rescore-retry/spec.md). A threshold change records
# the request with the change, and the rescoring after it clears it. The threshold is put back.
# The switch is ON for this: an earlier run that stopped halfway may have left it OFF.
expect 200 '"key":"rescore.auto","value":"ON"' -X PATCH -H 'Content-Type: application/json' -d '{"rescore.auto":"ON"}' "${BASE}/settings"
MINC=$(jq -r '.items[] | select(.key == "score.min_confidence") | .value' "${BODY}")
expect 200 '"key":"rescore.requested","value":""' "${PATCH[@]}" -d "{\"score.min_confidence\":$((MINC > 0 ? MINC - 1 : 1))}" "${BASE}/settings"
expect 200 '"key":"rescore.requested","value":""' "${PATCH[@]}" -d "{\"score.min_confidence\":\"${MINC}\"}" "${BASE}/settings"
# A request that is still there is carried out by the scheduler, which then clears it.
expect 200 "\"key\":\"rescore.requested\",\"value\":\"smoke ${STAG}\"" "${PATCH[@]}" -d "{\"rescore.requested\":\"smoke ${STAG}\"}" "${BASE}/settings"
expect 200 '"failed":\[\]' -X POST "${BASE}/scheduler/run"
if [[ "$(jq -c '.rescored >= 1' "${BODY}")" != "true" ]]; then
    echo "FAILED: the run should rescore the companies that have answers: $(cat "${BODY}")"
    exit 1
fi
expect 200 '"key":"rescore.requested","value":""' "${BASE}/settings"

# The switch for automatic rescoring (specs/040-rescore-switch/spec.md). While it is OFF a threshold
# change is stored and its rescoring waits, also through a scheduler run. ON again rescores once.
# The threshold is put back while the switch is OFF, and the switch is left ON.
expect 200 '"key":"rescore.auto","value":"OFF"' "${PATCH[@]}" -d '{"rescore.auto":"OFF"}' "${BASE}/settings"
expect 200 '"key":"rescore.requested","value":"2' "${PATCH[@]}" -d "{\"score.min_confidence\":$((MINC > 0 ? MINC - 1 : 1))}" "${BASE}/settings"
expect 200 '"rescored":0' -X POST "${BASE}/scheduler/run"
expect 200 '"key":"rescore.requested","value":"2' "${PATCH[@]}" -d "{\"score.min_confidence\":\"${MINC}\"}" "${BASE}/settings"
expect 400 '"error":"BAD_REQUEST"' "${PATCH[@]}" -d '{"rescore.auto":"MAYBE"}' "${BASE}/settings"
expect 200 '"key":"rescore.requested","value":""' "${PATCH[@]}" -d '{"rescore.auto":"ON"}' "${BASE}/settings"
expect 200 '"key":"rescore.auto","value":"ON"' "${BASE}/settings"

# Archiving hides a company and unarchiving brings it back; neither removes anything.
expect 200 '"archived_at":"2' -X POST "${BASE}/companies/${SOLO_ID}/archive"
expect 200 '"archived_at":null' -X POST "${BASE}/companies/${SOLO_ID}/unarchive"

# The contract (specs/032-openapi-models/spec.md): every successful answer above, and every body
# that was accepted, must fit the specification this Worker serves.
uv run python tools/contract_check.py "${SPEC}" "${CALLS}"
