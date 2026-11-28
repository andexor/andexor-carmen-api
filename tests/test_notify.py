# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Unit tests for the rules of notifications. See specs/015-scheduler-and-notifications/spec.md."""

import pathlib
import sys
import unittest
from datetime import UTC, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import notify  # noqa: E402

ZONE = "America/New_York"
DAYS = "MON,TUE,WED,THU,FRI"


class DigestWindow(unittest.TestCase):
    def test_the_digest_is_due_at_its_hour_and_later_in_the_local_day(self):
        # 2026-11-24 is in standard time: New York is five hours behind UTC.
        before = datetime(2026, 11, 24, 11, 59, tzinfo=UTC)  # 06:59 local
        at = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)  # 07:00 local
        late = datetime(2026, 11, 25, 4, 30, tzinfo=UTC)  # 23:30 local, still the 24th
        self.assertEqual(notify.digest_window(ZONE, "7", DAYS, before), (False, "2026-11-24T05:00:00Z"))
        self.assertEqual(notify.digest_window(ZONE, "7", DAYS, at), (True, "2026-11-24T05:00:00Z"))
        self.assertEqual(notify.digest_window(ZONE, "7", DAYS, late), (True, "2026-11-24T05:00:00Z"))

    def test_the_day_begins_at_local_midnight_in_daylight_time_too(self):
        summer = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)  # 08:00 local, four hours behind
        self.assertEqual(notify.digest_window(ZONE, "9", DAYS, summer), (False, "2026-07-01T04:00:00Z"))

    def test_the_digest_is_due_only_on_the_days_of_the_setting(self):
        # 2026-11-24 is a Tuesday. Noon UTC is 07:00 in New York.
        due = {day: notify.digest_window(ZONE, "7", DAYS, datetime(2026, 11, day, 12, 0, tzinfo=UTC))[0] for day in range(23, 30)}
        self.assertEqual(due, {23: True, 24: True, 25: True, 26: True, 27: True, 28: False, 29: False})
        saturday = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
        self.assertTrue(notify.digest_window(ZONE, "7", " sat , Sun", saturday)[0])
        self.assertFalse(notify.digest_window(ZONE, "7", "SAT", datetime(2026, 11, 27, 12, 0, tzinfo=UTC))[0])

    def test_the_day_is_the_local_day_not_the_day_in_utc(self):
        # Saturday 02:00 UTC is still Friday evening in New York.
        self.assertTrue(notify.digest_window(ZONE, "7", DAYS, datetime(2026, 11, 28, 2, 0, tzinfo=UTC))[0])

    def test_missing_or_unreadable_days_mean_monday_to_friday(self):
        for days in (None, "", "no days here", ","):
            self.assertTrue(notify.digest_window(ZONE, "7", days, datetime(2026, 11, 24, 12, 0, tzinfo=UTC))[0], days)
            self.assertFalse(notify.digest_window(ZONE, "7", days, datetime(2026, 11, 28, 12, 0, tzinfo=UTC))[0], days)

    def test_a_missing_zone_or_hour_falls_back(self):
        noon = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
        self.assertEqual(notify.digest_window(None, None, DAYS, noon), (True, "2026-11-24T00:00:00Z"))
        self.assertEqual(notify.digest_window("No/Such_Zone", "not an hour", DAYS, noon), (True, "2026-11-24T00:00:00Z"))
        self.assertEqual(notify.digest_window("UTC", "99", DAYS, noon)[0], True)  # 99 is no hour: the default is used


class Messages(unittest.TestCase):
    def test_a_digest_with_nothing_to_report_says_so(self):
        subject, body = notify.render("digest", date="2026-11-24", sections=notify.digest_sections({}) or notify.NOTHING)
        self.assertEqual(subject, "Carmen digest for 2026-11-24")
        self.assertEqual(body, "Nothing to report\n")

    def test_the_lists_are_empty_text_when_every_list_is_empty(self):
        self.assertEqual(notify.digest_sections({}), "")
        self.assertEqual(notify.digest_sections({"tasks_due": [], "leads_unscored": []}), "")

    def test_a_digest_shows_only_the_lists_that_hold_something(self):
        text = notify.digest_sections(
            {
                "tasks_overdue": [{"title": "Call back", "company_name": "Acme", "due_date": "2026-11-20"}],
                "agreements_renewing": [{"title": None, "type": "MSA", "company_name": "Acme", "end_date": "2026-12-01"}],
                "leads_unscored": [{"name": None, "domain": "new.example"}],
            }
        )
        self.assertEqual(
            text,
            "Tasks overdue (1)\n- Call back (Acme), due 2026-11-20\n\n"
            "Agreements in their renewal window (1)\n- MSA (Acme), ends 2026-12-01\n\n"
            "Leads waiting for a score (1)\n- new.example",
        )

    def test_proposals_show_the_score_and_the_answers_and_facts_behind_it(self):
        items = [
            {
                "name": "Web design",
                "need_score": 82,
                "confidence": 70,
                "answers": [{"prompt": "Is the site fast?", "value": "NO", "rationale": "It loads in 9 seconds"}],
                "facts": [{"key": "pagespeed_mobile", "value": "31"}],
            },
            {"name": "Hosting", "need_score": 40, "confidence": None, "answers": [{"prompt": "Is it up?", "value": "YES", "rationale": None}], "facts": []},
        ]
        text = notify.proposal_sections(items)
        self.assertEqual(
            text,
            "1. Web design: Need 82, confidence 70\n   - Is the site fast? NO. It loads in 9 seconds\n   Facts: pagespeed_mobile = 31"
            "\n\n2. Hosting: Need 40\n   - Is it up? YES",
        )

    def test_every_run_of_a_proposals_job_is_listed_with_its_model_and_its_result(self):
        runs = [
            {"kind": "RESEARCH", "model_label": "First", "status": "FAILED", "error": "The model refused"},
            {"kind": "RESEARCH", "model_label": "Second", "status": "SUCCEEDED", "error": None},
        ]
        self.assertEqual(notify.run_lines(runs), "- Research with First: failed. The model refused\n- Research with Second: succeeded")
        self.assertIn("No model could be used", notify.run_lines([]))
        # A run that was paid for a short while ago is not made again, and the email says so.
        earlier = [{"kind": "RESEARCH", "model_label": "First", "status": "SUCCEEDED", "error": None, "finished_at": "2026-11-28T14:05:00Z", "reused": True}]
        self.assertEqual(notify.run_lines(earlier), "- Research with First: succeeded at 2026-11-28T14:05:00Z, and was used again")

    def test_a_proposals_email_with_no_service_says_so_and_names_a_failed_run_first(self):
        self.assertEqual(notify.proposal_sections([]), "")
        subject, body = notify.render("proposals", company="Acme", title="Site build", note="Fresh research and scoring failed.\n\n", runs=notify.run_lines([]), sections=notify.NO_PROPOSALS)
        self.assertEqual(subject, "Proposals for Acme")
        self.assertTrue(body.startswith("Fresh research and scoring failed.\n\nThe runs of this job:\n- None. No model could be used.\n\nThe agreement Site build with Acme is completed."))
        self.assertTrue(body.endswith("No other service is a good match.\n"))

    def test_the_first_line_of_a_template_is_the_subject(self):
        subject, body = notify.render("digest", date="2026-11-24", sections="Tasks due today (1)\n- Call")
        self.assertEqual(subject, "Carmen digest for 2026-11-24")
        self.assertEqual(body, "Tasks due today (1)\n- Call\n")  # no greeting: the body starts with the first list

    def test_a_template_with_a_missing_value_or_no_file_is_an_error(self):
        with self.assertRaises(notify.NotifyError):
            notify.render("renewal", title="MSA")
        with self.assertRaises(notify.NotifyError):
            notify.render("no-such-template")

    def test_braces_in_a_record_do_not_fail_an_email(self):
        subject, body = notify.render("digest", date="2026-11-24", sections="Leads (1)\n- Acme {{x and {{date}}")
        self.assertEqual(subject, "Carmen digest for 2026-11-24")
        self.assertIn("- Acme {{x and {{date}}", body)  # kept as typed, and not filled a second time

    def test_the_renewal_alert_names_the_agreement_and_its_dates(self):
        subject, body = notify.render(
            "renewal",
            heading="Ending",
            title="MSA",
            company="Acme",
            end_date="2026-12-24",
            notice_date="2026-11-24",
            what_happens="It is marked completed.",
        )
        self.assertEqual(subject, "Ending: MSA ends 2026-12-24")
        self.assertIn("with Acme ends on 2026-12-24. It is marked completed.", body)
        self.assertIn('A task "Ending: MSA" is on your list, due 2026-11-24.', body)


class Providers(unittest.TestCase):
    def test_a_subject_gets_the_prefix_of_its_environment_and_none_where_there_is_none(self):
        self.assertEqual(notify.subject_line("PREV", "Carmen digest for 2026-11-25"), "PREV: Carmen digest for 2026-11-25")
        for prefix in (None, ""):
            self.assertEqual(notify.subject_line(prefix, "Carmen digest for 2026-11-25"), "Carmen digest for 2026-11-25")

    def test_a_side_of_a_message_carries_its_name_and_a_bare_address_stays_bare(self):
        self.assertEqual(notify.party("Carmen PREV <carmen@test.example>"), {"email": "carmen@test.example", "name": "Carmen PREV"})
        self.assertEqual(notify.party('"Last, First" <someone@test.example>'), {"email": "someone@test.example", "name": "Last, First"})
        self.assertEqual(notify.party("someone@test.example"), "someone@test.example")
        self.assertEqual(notify.party(" <someone@test.example> "), "someone@test.example")

    def test_only_a_named_provider_sends_and_anything_else_is_the_stub(self):
        self.assertEqual(notify.provider("CLOUDFLARE").status, "SENT")
        for name in ("STUB", "", None, "undefined", "cloudflare"):
            self.assertEqual(notify.provider(name).status, "STUBBED", name)


if __name__ == "__main__":
    unittest.main()
