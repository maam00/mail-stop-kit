import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from mail_stop.ledger import Ledger, validate_event
from mail_stop.policy import InputError, plan
from mail_stop.routes import attach, directory, next_step, template
from test_policy import HOUSEHOLD, piece


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "private")
        self.ident = self.ledger.add_plan(plan(HOUSEHOLD, [piece(sender="Valpak")]))[0]
        self.route = template("valpak")
        self.today = date(2026, 10, 19)

    def tearDown(self):
        self.temp.cleanup()

    def verified(self, **changes):
        return dict(self.route, verification="verified", scope_confirmed=True, authority_confirmed=True, steps_confirmed=True, **changes)

    def next(self, today=None):
        return next_step(self.ledger.get(self.ident), today or self.today)

    def record(self, event, source="user_report", evidence="Fictional evidence", **options):
        return self.ledger.record(self.ident, validate_event(event, source, evidence, **options))

    def test_directory_is_research_not_verified_household_action(self):
        entries = directory()["routes"]
        self.assertGreaterEqual(len(entries), 5)
        self.assertTrue(all(e["verification"] == "researched" and not e["authority_confirmed"] for e in entries))
        self.assertEqual(self.next()["next"], "verify_route")
        attach(self.ledger, self.ident, self.route, self.today)
        self.assertEqual(self.next()["next"], "verify_route")

    def test_verification_requires_explicit_scope_and_authority(self):
        for changes in ({"scope_confirmed": False}, {"authority_confirmed": False}):
            bad = dict(self.verified(), **changes)
            with self.assertRaises(InputError):
                attach(self.ledger, self.ident, bad, self.today)

    def test_similar_brand_or_wrong_business_scope_is_refused(self):
        for value in (template("save"), template("llbean-business"), dict(self.route, sender_exact="Valpak-like offers"),
                      dict(self.route, provider_scope="Consumer credit account changes")):
            with self.assertRaises(InputError):
                attach(self.ledger, self.ident, value, self.today)
        consumer = self.ledger.add_plan(plan(HOUSEHOLD, [piece(sender="L.L.Bean")]))[0]
        with self.assertRaises(InputError):
            attach(self.ledger, consumer, template("llbean-business"), self.today)

    def test_required_steps_and_expired_source_keep_case_out_of_ready(self):
        attach(self.ledger, self.ident, dict(self.verified(), steps_confirmed=False), self.today)
        self.assertEqual(self.next()["next"], "required_user_steps")
        attach(self.ledger, self.ident, self.verified(), self.today)
        self.assertEqual(self.next()["next"], "ready_for_review")
        self.assertEqual(self.next(self.today + timedelta(days=31))["next"], "verify_route")

    def test_exact_duplicate_is_idempotent_but_revisions_preserve_history(self):
        self.assertTrue(attach(self.ledger, self.ident, self.route, self.today))
        self.assertFalse(attach(self.ledger, self.ident, self.route, self.today))
        self.assertTrue(attach(self.ledger, self.ident, self.verified(), self.today))
        self.assertTrue(attach(self.ledger, self.ident, self.route, self.today))
        self.assertEqual(len(self.ledger.get(self.ident)["routes"]), 3)

    def test_submission_waits_instead_of_becoming_ready_for_duplicate(self):
        attach(self.ledger, self.ident, self.verified(), self.today)
        self.record("submitted")
        self.assertEqual(self.next()["next"], "waiting")
        self.record("acknowledged", "provider_message")
        self.assertEqual(self.next()["next"], "waiting")

    def test_confirmation_step_is_explicit_and_never_a_submission_or_change(self):
        route = self.verified()
        route.pop("directory_id")
        route["confirmation_required"] = True
        attach(self.ledger, self.ident, route, self.today)
        with self.assertRaises(InputError):
            self.record("confirmation_completed")
        self.record("submitted")
        self.assertEqual(self.next()["next"], "confirmation_needed")
        self.record("confirmation_completed", evidence="Fictional user completed the provider's verification step")
        self.assertEqual(self.next()["next"], "waiting")
        self.assertEqual(self.ledger.get(self.ident)["summary"]["state"], "submission_recorded")

    def test_user_review_date_is_not_an_automatic_resend_deadline(self):
        route = self.verified(review_on="2026-10-20")
        attach(self.ledger, self.ident, route, self.today)
        self.record("submitted")
        self.assertEqual(self.next()["next"], "waiting")
        result = self.next(date(2026, 10, 20))
        self.assertEqual(result["next"], "review_due")
        self.assertFalse(result["automatic_action"])
        self.assertIn("not a provider failure deadline", result["instruction"])

    def test_uncertainty_wins_over_due_date(self):
        attach(self.ledger, self.ident, self.verified(review_on="2026-10-19"), self.today)
        self.record("uncertain", "local_note")
        self.assertEqual(self.next()["next"], "reconcile")

    def test_later_mail_observation_is_review_not_a_new_send(self):
        attach(self.ledger, self.ident, self.verified(), self.today)
        self.record("submitted")
        with patch("mail_stop.ledger.now", return_value="2026-10-17T12:00:00+00:00"):
            self.record("change_reported", "provider_message")
        self.record("mail_observed", "selected_digest", observed_on="2026-10-18")
        self.assertEqual(self.next()["next"], "review_new_observation")
        self.assertFalse(self.next()["automatic_action"])
        self.record("closed")
        self.record("mail_observed", "selected_digest", evidence="Another fictional observation", observed_on="2026-10-19")
        self.assertEqual(self.next()["next"], "closed")
        with self.assertRaises(InputError):
            attach(self.ledger, self.ident, self.verified(), self.today)

    def test_bad_destination_types_dates_urls_and_purpose_are_controlled(self):
        for changes in ({"channel": []}, {"destination": []}, {"checked_on": "wrong"}, {"checked_on": "2030-01-01"},
                        {"source_url": "https://[bad"}, {"source_url": "https://user:password@example.invalid"},
                        {"scope_confirmed": "true"}, {"purpose": "change_other_person_account"}, {"required_steps": []}):
            with self.subTest(changes=changes), self.assertRaises(InputError):
                attach(self.ledger, self.ident, dict(self.route, **changes), self.today)

    def test_known_physical_enclosure_is_not_silently_removed(self):
        ident = self.ledger.add_plan(plan(HOUSEHOLD, [piece(sender="L.L.Bean")]))[0]
        route = template("llbean-consumer-postal")
        self.assertTrue(route["requires_physical_enclosure"])
        with self.assertRaises(InputError):
            attach(self.ledger, ident, dict(route, requires_physical_enclosure=False), self.today)

    def test_v2_to_v3_migration_is_atomic_and_preserves_case(self):
        with self.ledger.db() as db:
            db.executescript("DROP TABLE routes; PRAGMA user_version=2;")
        original = sqlite3.connect
        class Broken(sqlite3.Connection):
            def executescript(self, script):
                return super().executescript(script.replace("PRAGMA user_version=3;", "INVALID INTERRUPT; PRAGMA user_version=3;"))
        with patch("mail_stop.ledger.sqlite3.connect", side_effect=lambda *a, **k: original(*a, **k, factory=Broken)):
            with self.assertRaises(sqlite3.Error):
                Ledger(self.ledger.folder)
        with self.ledger.db() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='routes'").fetchone())
        reopened = Ledger(self.ledger.folder)
        self.assertEqual(reopened.get(self.ident)["request"]["id"], self.ident)


if __name__ == "__main__":
    unittest.main()
