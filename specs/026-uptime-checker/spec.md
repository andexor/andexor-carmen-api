<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Feature Specification: Uptime checker

**Feature Branch**: `56-uptime-checker`

**Created**: 2026-11-26

**Status**: Implemented, and live on the preview.

**Input**: Issue #56. Parked on 2026-11-24 in `NOTES.md`: our own scheduled uptime checker, so that "Is the site reliably up?" is answered from our own measurements and not left unknown (`specs/021-measured-criteria/spec.md`, FR-010). Decided 2026-11-26: a site is checked once a day; a company is measured for 14 days and then its checks stop, until a person starts a new measurement; and 99 percent or more of successful checks is YES. Where this spec and an earlier document differ, this spec governs.

## The design

```
   Cron Trigger, every hour  ->  run_scheduler  ->  step "uptime"
                                                        |
        every lead and prospect with a public website, fewer than 14 checks,
        and no check in the last 23 hours (at most 25 sites in one run; both are settings)
                                                        |
                                                        v
        one request to the website; a failure is tried a second time
                                                        |
                                                        v
        uptime_check row  +  the fact uptime_percent, when the share changed
                                                        |
                                                        v
        the next scoring run answers "Is the site reliably up?" from the fact:
        99 or more is YES, less is NO
```

## User Scenarios and Testing

### User Story 1 - A site is checked once a day (Priority: P1)

**Independent Test**: `./preview-test.sh` creates a lead with a website, runs the scheduler twice, and reads the checks.

**Acceptance Scenarios**:

1. **Given** a lead or a prospect with a public website and no check yet,\
   **When** the scheduler runs,\
   **Then** its website is checked once and the check is stored.
2. **Given** a company that was checked less than 23 hours ago,\
   **When** the scheduler runs again,\
   **Then** no second check is made.
3. **Given** a company with as many checks as the setting `uptime.window_days` says,\
   **When** the scheduler runs,\
   **Then** it is not checked again.
4. **Given** a site that does not answer, or answers 500 or above,\
   **When** it is checked,\
   **Then** it is tried a second time, and counts as down only when that fails too.

### User Story 2 - The question is answered from the checks (Priority: P1)

**Independent Test**: `./unit-test.sh` covers the share and when the fact is written. `./preview-test.sh` reads the fact.

**Acceptance Scenarios**:

1. **Given** a check was stored,\
   **When** the share of successful checks differs from the stored fact,\
   **Then** a new fact `uptime_percent` is stored with the source `UPTIME_CHECK`, from the first check on.
2. **Given** a company with the fact `uptime_percent`,\
   **When** a scoring run is made,\
   **Then** "Is the site reliably up?" is `YES` at 99 or more and `NO` below, and no model is asked.

### User Story 3 - A person reads the checks and starts a new measurement (Priority: P2)

**Independent Test**: `./preview-test.sh` reads the checks, starts a new measurement, and sees the earlier check replaced.

**Acceptance Scenarios**:

1. **Given** a company,\
   **When** its uptime is read,\
   **Then** the answer holds `percent`, `checks`, `up`, `window_days`, `measuring`, and the checks, newest first.
2. **Given** a lead or a prospect with a public website,\
   **When** a new measurement is started,\
   **Then** the earlier checks are deleted, which is recorded, one check is made now, and the scheduler makes the others.
3. **Given** a customer, a disqualified or archived company, or a company with no public website,\
   **When** a new measurement is started,\
   **Then** the answer is 409.

### Edge Cases

- Up means the site answered with a status below 500. A refusal (403) or a missing page (404) still shows a server that works. No answer in 10 seconds, a name that does not resolve, and 500 and above are down. For a name that does not resolve the Workers runtime answers 530 and raises nothing; the check says "the site could not be reached (530)".
- With one check a day, one failed check in 14 is 92.9 percent, which is `NO` at the limit of 99. The limit is data, in the criterion's `config_json`.
- The days are counted in checks: a company is done when it has `uptime.window_days` checks. A day that was missed is made up, so the measurement takes longer and is not shorter.
- A company that becomes a customer, is disqualified, or is archived is no longer checked. Its checks and its fact stay.
- A company that was never checked comes first, the newest first. One run checks at most `uptime.sites_per_run` sites (25); the rest wait for the next hour.
- The answer to the question changes at the next scoring run, not when the fact is stored (`specs/021-measured-criteria/spec.md`, "Not in this feature").
- The checks of a company are deleted with the company.
- A website that is not a public web address is never fetched (`pages.site_url`).

## Requirements

### Functional Requirements

- **FR-001**: The scheduler has a step `uptime`, after the agreements end and before anything is sent. It runs with no `digest.recipient` too. A failure of the step is named in `failed` and stops no other step. The run answers `uptime`: the number of sites checked.
- **FR-002**: A company is due when its stage is `LEAD` or `PROSPECT`, it is not archived, it has a public website, it has fewer checks than `uptime.window_days`, and its last check is more than `uptime.hours_between_checks` hours old or it has none. One run checks at most `uptime.sites_per_run` sites.
- **FR-003**: A check is one request for the website's address, with the headers and the 10 seconds of the page reader. It never raises. A check that fails is made a second time at once.
- **FR-004**: A check is stored as an `uptime_check` row: `up`, `status`, `duration_ms`, and for a failure `detail`, a reason that is safe to show. The detail of an error goes to the Worker log.
- **FR-005**: With each check, the share of successful checks of the company, 0 to 100 with one decimal, is stored as a new fact `uptime_percent` when it differs from the latest one. Its source is `UPTIME_CHECK`, and its note says how many checks succeeded. All checks and facts of one run are stored in one batch.
- **FR-006**: `GET /api/v1/companies/{id}/uptime` answers the company's uptime as in User Story 3. `measuring` says whether the scheduler will check the site again.
- **FR-007**: `POST /api/v1/companies/{id}/uptime` starts a new measurement. The delete of the earlier checks is recorded as every delete is: the Worker log first, then the company's timeline, in the same batch as the delete and the new check.
- **FR-008**: The rule for the answer is data: migration `0015_uptime_checks` gives "Is the site reliably up?" the fact `uptime_percent` and `{"yes_at": 99}`. The code of `specs/021-measured-criteria/spec.md` answers it.
- **FR-009**: The checker has three settings: `uptime.window_days` (14), `uptime.sites_per_run` (25), and `uptime.hours_between_checks` (23). The stored setting is used first. The value in brackets is a constant in code, used only when the setting is missing or cannot be read as a whole number of at least 1. `PATCH /api/v1/settings` refuses any other value for a setting whose key begins with `uptime.`.

### Endpoints

| Method and path | Action | Does |
| --- | --- | --- |
| `GET /api/v1/companies/{id}/uptime` | `READ` | The share of successful checks, and the checks, newest first |
| `POST /api/v1/companies/{id}/uptime` | `WRITE` | Starts a new measurement: deletes the earlier checks and makes one check now |

### Data model

Migration `0015_uptime_checks` in `andexor-carmen-main`: the table `uptime_check`, the fact key `uptime_percent`, the fact source `UPTIME_CHECK`, the setting `uptime.window_days` (14), and the measured rule of the question. Migration `0016_uptime_settings`: the settings `uptime.sites_per_run` (25) and `uptime.hours_between_checks` (23).

### Not in this feature

- Checking customers. For a site we manage, the plan is Grafana.
- A check more often than once a day, or from more than one place.
- Third-party uptime sources, PageSpeed Insights, and the Chrome UX Report. They stay parked in `NOTES.md`.
- Applying the answer to the company's scores when the fact changes, outside a scoring run.

### What is not tested

- 14 days of checks, and so the stop at the last one. The rule is one condition in the query of FR-002.
- A scoring run that answers the question from the fact. The code is that of feature 021 and has its unit tests; only the criterion's data changed.
- More sites due in one run than `uptime.sites_per_run`.
- A site that is up, in the smoke test: it checks only a name under `.example`, so that no real site is called. By hand on the preview on 2026-11-26: `www.mozilla.org` and `www.cloudflare.com` answered 200 and gave 100 percent, and `no-such-site-zz9.example` gave 0 percent. Those three companies, named "Uptime review", are left on the preview.

## Success Criteria

- **SC-001**: `./unit-test.sh` passes, with no type errors.
- **SC-002**: `./preview-test.sh` passes: a scheduler run checks a new lead's site once and stores the fact, a second run makes no second check, a new measurement replaces the earlier check (the company and its checks stay on the preview, to be looked at), and a company with no website and an unknown company are refused.
