import copy
from contextlib import closing
import os
import sqlite3
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mail_stop.ledger import Ledger, validate_event
from mail_stop.policy import InputError, plan
from test_policy import HOUSEHOLD, EXAMPLES, piece


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name) / "private"
        self.ledger = Ledger(self.folder)
        self.plan = plan(HOUSEHOLD, [piece()])
        self.ident = self.ledger.add_plan(self.plan)[0]

    def tearDown(self):
        self.temp.cleanup()

    def record(self, kind, source="user_report", evidence="Synthetic evidence", **options):
        return self.ledger.record(self.ident, validate_event(kind, source, evidence, **options))

    def test_prepare_is_idempotent_and_preserves_new_observations(self):
        self.assertEqual(self.ledger.add_plan(self.plan), [self.ident])
        self.assertEqual(len(self.ledger.get(self.ident)["observations"]), 1)
        self.ledger.add_plan(plan(HOUSEHOLD, [piece(id="second-observation")]))
        self.assertEqual(len(self.ledger.get(self.ident)["observations"]), 2)
        self.assertEqual(len(self.ledger.list()), 1)
        self.assertEqual(self.ledger.get(self.ident)["summary"]["state"], "draft")

    def test_changed_draft_never_overwrites_prior_evidence(self):
        changed = copy.deepcopy(self.plan)
        changed["pieces"][0]["request"]["body"] = "Changed demand"
        with self.assertRaises(InputError):
            self.ledger.add_plan(changed)
        self.assertEqual(self.ledger.get(self.ident)["request"]["body"], self.plan["pieces"][0]["request"]["body"])

    def test_batch_is_atomic_on_conflicting_draft(self):
        changed = plan(HOUSEHOLD, [piece(id="new", sender="Second Example Sender"), piece()])
        changed["pieces"][1]["request"]["body"] = "Changed demand"
        with self.assertRaises(InputError):
            self.ledger.add_plan(changed)
        self.assertEqual(len(self.ledger.list()), 1)

    def test_receipts_do_not_prove_reduction(self):
        self.record("submitted")
        self.record("acknowledged", "provider_message", "Synthetic request received notice")
        self.record("change_reported", "provider_message", "Synthetic advertiser says list updated")
        result = self.ledger.get(self.ident)
        self.assertEqual(result["summary"]["state"], "provider_reported_change")
        self.assertFalse(result["summary"]["mail_reduction_verified"])

    def test_test_mode_cannot_be_turned_into_delivery_or_change(self):
        self.record("test_submitted", "local_note", "Synthetic Lob test-mode ID")
        self.assertEqual(self.ledger.get(self.ident)["summary"]["state"], "test_only")
        for event in ("acknowledged", "change_reported"):
            with self.assertRaises(InputError):
                self.record(event, "provider_message")

    def test_uncertain_needs_reconciliation_not_an_assumed_acknowledgment(self):
        self.record("uncertain", "local_note", "Submission outcome was not confirmed")
        with self.assertRaises(InputError):
            self.record("acknowledged", "provider_message")
        with self.assertRaises(InputError):
            self.record("test_submitted", "local_note")
        self.record("submitted", "provider_message", "Existing receipt located; no resend")
        self.assertEqual(self.ledger.get(self.ident)["summary"]["state"], "submission_recorded")

    def test_duplicate_or_late_submission_is_not_another_attempt(self):
        self.assertTrue(self.record("submitted"))
        self.assertFalse(self.record("submitted"))
        with self.assertRaises(InputError):
            self.record("submitted", evidence="Another send")
        self.assertEqual(len(self.ledger.get(self.ident)["events"]), 1)

    def test_concurrent_same_receipt_records_once(self):
        event = validate_event("submitted", "user_report", "Same synthetic receipt")
        with ThreadPoolExecutor(max_workers=2) as pool:
            added = list(pool.map(lambda _: self.ledger.record(self.ident, event), range(2)))
        self.assertEqual(sorted(added), [False, True])

    def test_followup_observation_does_not_erase_change_report(self):
        self.record("submitted")
        self.record("change_reported", "provider_message")
        self.record("mail_observed", "selected_digest", "Seen in selected digest, physical delivery unverified", observed_on="2026-10-19")
        result = self.ledger.get(self.ident)
        self.assertEqual(result["summary"]["state"], "provider_reported_change")
        self.assertEqual(result["summary"]["latest_mail_observation"]["source"], "selected_digest")
        self.assertFalse(result["summary"]["mail_reduction_verified"])

    def test_provider_report_cannot_be_downgraded(self):
        self.record("submitted")
        self.record("change_reported", "provider_message")
        with self.assertRaises(InputError):
            self.record("acknowledged", "provider_message")

    def test_latest_observation_uses_observation_date_not_import_order(self):
        self.record("mail_observed", "selected_digest", "Recent synthetic digest", observed_on="2026-10-19")
        self.record("mail_observed", "selected_digest", "Older synthetic digest imported later", observed_on="2026-10-01")
        latest = self.ledger.get(self.ident)["summary"]["latest_mail_observation"]
        self.assertEqual(latest["observed_on"], "2026-10-19")

    def test_user_closure_does_not_reopen_or_mean_suppression(self):
        self.record("closed", evidence="Household is done with this request")
        with self.assertRaises(InputError):
            self.record("submitted")
        self.record("mail_observed", "user_report", "Another envelope observed", observed_on="2026-10-19")
        self.assertEqual(self.ledger.get(self.ident)["summary"]["state"], "closed_by_user")

    def test_evidence_source_and_dates_are_checked(self):
        for args in (("acknowledged", "user_report"), ("change_reported", "local_note"), ("submitted", "selected_digest")):
            with self.assertRaises(InputError):
                validate_event(*args, "Synthetic evidence")
        with self.assertRaises(InputError):
            validate_event("mail_observed", "user_report", "Synthetic evidence")
        with self.assertRaises(InputError):
            validate_event("mail_observed", "user_report", "Synthetic evidence", observed_on="2026-99-99")

    def test_no_events_for_missing_request(self):
        with self.assertRaises(InputError):
            self.ledger.record("missing", validate_event("submitted", "user_report", "Synthetic evidence"))

    @unittest.skipUnless(os.name == "posix", "POSIX permission assertion")
    def test_private_file_permissions(self):
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.ledger.path.stat().st_mode), 0o600)

    def test_existing_unrelated_directory_is_not_modified(self):
        other = Path(self.temp.name) / "other"
        other.mkdir()
        (other / "important.txt").write_text("Keep me")
        before = other.stat().st_mode
        with self.assertRaises(InputError):
            Ledger(other)
        self.assertEqual((other / "important.txt").read_text(), "Keep me")
        self.assertEqual(other.stat().st_mode, before)

    def test_existing_other_database_is_not_repurposed(self):
        other = Path(self.temp.name) / "otherdb"
        other.mkdir()
        path = other / "mail-stop.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE unrelated(value TEXT)")
        before = path.read_bytes()
        modes = (other.stat().st_mode, path.stat().st_mode)
        with self.assertRaises(InputError):
            Ledger(other)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((other.stat().st_mode, path.stat().st_mode), modes)

    def test_version_one_does_not_make_an_unrelated_database_ours(self):
        other = Path(self.temp.name) / "foreign-v1"
        other.mkdir()
        path = other / "mail-stop.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE unrelated(value TEXT)")
            db.execute("PRAGMA user_version=1")
        before, modes = path.read_bytes(), (other.stat().st_mode, path.stat().st_mode)
        with self.assertRaises(InputError):
            Ledger(other)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((other.stat().st_mode, path.stat().st_mode), modes)

    def test_matching_table_names_with_wrong_columns_are_rejected(self):
        other = Path(self.temp.name) / "foreign-columns"
        other.mkdir()
        path = other / "mail-stop.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.executescript("CREATE TABLE requests(id TEXT); CREATE TABLE observations(hash TEXT); "
                             "CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT); PRAGMA user_version=1;")
        before = path.read_bytes()
        with self.assertRaises(InputError):
            Ledger(other)
        self.assertEqual(path.read_bytes(), before)

    @unittest.skipUnless(os.name == "posix", "symlink support")
    def test_symlink_folder_or_database_is_refused(self):
        link = Path(self.temp.name) / "linked"
        link.symlink_to(self.folder, target_is_directory=True)
        with self.assertRaises(InputError):
            Ledger(link)
        other = Path(self.temp.name) / "linked-db"
        other.mkdir()
        (other / "mail-stop.sqlite3").symlink_to(self.ledger.path)
        with self.assertRaises(InputError):
            Ledger(other)


if __name__ == "__main__":
    unittest.main()
