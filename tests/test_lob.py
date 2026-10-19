"""Offline fault injection only. Every address/key/receipt below is fictional."""
import copy
from contextlib import closing
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mail_stop import lob
from mail_stop.routes import attach as attach_route
from mail_stop.ledger import Ledger, validate_event
from mail_stop.policy import InputError, office_draft, plan
from test_policy import HOUSEHOLD, EXAMPLES, piece

ADDRESS = {"name": "Alex Example", "address_line1": "100 Example Lane", "address_line2": "Unit 4",
           "address_city": "Exampletown", "address_state": "FL", "address_zip": "00000"}
DESTINATION = {"name": "Example Sender", "address_line1": "99 Example Postal Way",
               "address_city": "Exampletown", "address_state": "FL", "address_zip": "00000"}
ADDRESSES = {"from": ADDRESS, "to": DESTINATION, "destination_verified": True}
CLOCK = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    def request(self, method, path, key, **kwargs):
        self.calls.append((method, path, kwargs))  # Keys deliberately absent from captured evidence.
        if self.callback:
            return self.callback(method, path, **kwargs)
        raise AssertionError("No synthetic provider response configured")


@unittest.skipUnless(importlib.util.find_spec("reportlab"), "Install .[lob] for adapter/PDF tests")
class LobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.ledger = Ledger(self.folder / "private")
        self.request_id = self.ledger.add_plan(plan(HOUSEHOLD, [piece()]))[0]
        self.key = self.folder / "test-key"
        self.key.write_text("test_FICTIONAL0123456789")
        self.key.chmod(0o600)
        self.live_key = self.folder / "live-key"
        self.live_key.write_text("live_FICTIONAL0123456789")
        self.live_key.chmod(0o600)
        self.route = {"sender_exact": self.ledger.get(self.request_id)["request"]["sender"], "provider_scope": "Fictional postal advertising",
                      "purpose": "promotional_postal_mail", "channel": "postal", "destination": "99 Example Postal Way Exampletown FL 00000",
                      "source_url": "https://example.invalid/postal-removal", "checked_on": CLOCK.date().isoformat(), "verification": "verified",
                      "scope_confirmed": True, "authority_confirmed": True, "eligibility": "Fictional authorized current occupant",
                      "required_steps": ["Review the fictional sender and address."], "steps_confirmed": True,
                      "confirmation_required": False, "requires_physical_enclosure": False}
        attach_route(self.ledger, self.request_id, self.route)
        self.job = lob.prepare(self.ledger, self.request_id, ADDRESSES)
        self.ident = self.job["id"]

    def tearDown(self):
        self.temp.cleanup()

    def approve(self, ident=None):
        ident = ident or self.ident
        value = lob.status(self.ledger, ident)
        return lob.approve(self.ledger, ident, value["digest"], reviewed=True,
                           accept_variable_price=value["mode"] == "live", clock=CLOCK)

    def receipt(self, ident=None, **changes):
        info = lob.status(self.ledger, ident or self.ident)
        value = {"id": "ltr_FICTIONAL012345", "object": "letter", "status": "processed",
                 "metadata": {"mail_stop_id": info["id"], "mail_stop_digest": info["digest"]},
                 "to": info["manifest"]["to"], "from": info["manifest"]["from"],
                 "url": "https://example.invalid/private-signed-asset?secret=fictional"}
        value.update(changes)
        return value

    def accepted(self, ident=None):
        return FakeTransport(lambda *a, **k: self.receipt(ident))

    def price(self, **changes):
        value = {"estimated_total_usd": "99.00", "checked_at": CLOCK.isoformat(),
                 "source_url": "https://dashboard.lob.com/", "account_plan": "Fictional test plan, not a quote",
                 "content_pages": 1, "billable_pages": 2, "service": "none",
                 "assumptions": "Synthetic estimate only; address page, service and taxes reviewed by a fictional operator."}
        value.update(changes)
        return value

    def test_prepare_is_local_and_test_default_with_no_price_cap_claim(self):
        self.assertEqual(self.job["mode"], "test")
        self.assertEqual(self.job["state"], "prepared")
        self.assertEqual(self.job["manifest"]["billable_pages"], 2)
        self.assertIn("not an enforceable cap", self.job["manifest"]["price_note"])
        self.assertEqual(lob.prepare(self.ledger, self.request_id, ADDRESSES)["digest"], self.job["digest"])

    def test_approval_requires_exact_digest_and_review(self):
        for checksum, reviewed in ((self.job["digest"], False), ("changed", True)):
            with self.assertRaises(InputError):
                lob.approve(self.ledger, self.ident, checksum, reviewed=reviewed)
        with self.assertRaises(InputError):
            lob.send(self.ledger, self.ident, self.key, transport=self.accepted())

    def test_preview_equals_uploaded_bytes_and_durable_claim_precedes_transport(self):
        preview = lob.preview(self.ledger, self.ident)
        pdf = Path(preview["content_pdf"]).read_bytes()
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertEqual(lob.digest(pdf), self.job["manifest"]["pdf_sha256"])
        self.approve()

        def inspect(method, path, **kwargs):
            with closing(sqlite3.connect(self.ledger.path)) as db:
                state, claimed, key_hash = db.execute("SELECT state,claimed_at,key_hash FROM lob_jobs WHERE id=?", (self.ident,)).fetchone()
            self.assertEqual(state, "submitting")
            self.assertTrue(claimed)
            self.assertTrue(key_hash)
            self.assertEqual(kwargs["document"], pdf)
            self.assertEqual(kwargs["idempotency"], self.ident)
            self.assertEqual(kwargs["fields"]["address_placement"], "insert_blank_page")
            return self.receipt()

        result = lob.send(self.ledger, self.ident, self.key, transport=FakeTransport(inspect))
        self.assertEqual(result["state"], "accepted")
        self.assertIn("no physical mail", result["status"])
        self.assertNotIn("url", result["receipt"])
        self.assertFalse(result["mail_reduction_verified"])
        self.assertEqual(self.ledger.get(self.request_id)["lob_mailings"][0]["state"], "accepted")

    def test_modified_export_does_not_change_saved_pdf_or_send(self):
        preview = lob.preview(self.ledger, self.ident)
        path = Path(preview["content_pdf"])
        original = path.read_bytes()
        path.write_bytes(b"changed")
        with self.assertRaises(InputError):
            lob.preview(self.ledger, self.ident)
        self.approve()
        client = self.accepted()
        lob.send(self.ledger, self.ident, self.key, transport=client)
        self.assertEqual(client.calls[0][2]["document"], original)

    def test_saved_document_tampering_refused(self):
        self.approve()
        with self.ledger.db() as db:
            db.execute("UPDATE lob_jobs SET document=? WHERE id=?", (b"changed", self.ident))
        with self.assertRaises(InputError):
            lob.send(self.ledger, self.ident, self.key, transport=self.accepted())

    def test_timeout_locks_without_raw_error_or_new_attempt(self):
        self.approve()
        def fail(*a, **k):
            raise TimeoutError("PRIVATE PROVIDER BODY AND KEY MUST NOT LEAK")
        client = FakeTransport(fail)
        with self.assertRaises(InputError) as error:
            lob.send(self.ledger, self.ident, self.key, transport=client)
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertEqual(lob.status(self.ledger, self.ident)["state"], "uncertain")
        for action in (lambda: lob.send(self.ledger, self.ident, self.key, transport=client),
                       lambda: self.approve(),
                       lambda: lob.prepare(self.ledger, self.request_id, ADDRESSES, replace=True)):
            with self.assertRaises(InputError):
                action()
        self.assertEqual(len(client.calls), 1)

    def test_process_death_leaves_claimed_state_and_permanent_retry_lock(self):
        self.approve()
        def die(*a, **k):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            lob.send(self.ledger, self.ident, self.key, transport=FakeTransport(die))
        with self.ledger.db() as db:
            db.execute("UPDATE lob_jobs SET claimed_at='2020-01-01T00:00:00+00:00' WHERE id=?", (self.ident,))
        self.assertEqual(lob.status(self.ledger, self.ident)["state"], "submitting")
        with self.assertRaises(InputError):
            lob.send(self.ledger, self.ident, self.key, transport=self.accepted())

    def test_concurrent_senders_make_one_post(self):
        self.approve()
        client = self.accepted()
        def submit(_):
            try:
                return lob.send(self.ledger, self.ident, self.key, transport=client)["state"]
            except InputError:
                return "blocked"
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(outcomes), ["accepted", "blocked"])
        self.assertEqual(len(client.calls), 1)

    def make_uncertain(self):
        self.approve()
        with self.assertRaises(InputError):
            lob.send(self.ledger, self.ident, self.key, transport=FakeTransport(lambda *a, **k: {}))

    def test_reconciliation_uses_metadata_filter_and_never_posts(self):
        self.make_uncertain()
        client = FakeTransport(lambda *a, **k: {"data": [self.receipt()], "next_url": None})
        result = lob.reconcile(self.ledger, self.ident, self.key, transport=client)
        self.assertEqual(result["state"], "accepted")
        self.assertEqual(client.calls[0][0], "GET")
        self.assertIn(self.ident, client.calls[0][1])
        self.assertIn("metadata%5Bmail_stop_id%5D", client.calls[0][1])
        again = self.accepted()
        lob.reconcile(self.ledger, self.ident, self.key, transport=again)
        self.assertEqual(again.calls[0][1], "/ltr_FICTIONAL012345")

    def test_zero_multiple_paginated_or_mismatched_matches_remain_locked(self):
        self.make_uncertain()
        for response in ({"data": []}, {"data": [self.receipt(), self.receipt()]},
                         {"data": [self.receipt()], "next_url": "https://example.invalid"},
                         {"data": [self.receipt(metadata={})]}, {"data": [self.receipt(id="wrong")]}):
            with self.subTest(response_shape=list(response)):
                with self.assertRaises(InputError):
                    lob.reconcile(self.ledger, self.ident, self.key, transport=FakeTransport(lambda *a, **k: response))
                self.assertEqual(lob.status(self.ledger, self.ident)["state"], "uncertain")

    def test_receipt_wrong_unit_or_name_refused(self):
        self.approve()
        changed = copy.deepcopy(self.receipt())
        changed["to"]["address_line2"] = "Unit 9"
        with self.assertRaises(InputError):
            lob.send(self.ledger, self.ident, self.key, transport=FakeTransport(lambda *a, **k: changed))
        self.assertEqual(lob.status(self.ledger, self.ident)["state"], "uncertain")

    def test_key_mode_permissions_symlink_and_account_separation(self):
        self.approve()
        with self.assertRaises(InputError):
            lob.send(self.ledger, self.ident, self.live_key, transport=self.accepted())
        if os.name == "posix":
            self.key.chmod(0o644)
            with self.assertRaises(InputError):
                lob.read_key(self.key, "test")
            self.key.chmod(0o600)
            link = self.folder / "key-link"
            link.symlink_to(self.key)
            with self.assertRaises(InputError):
                lob.read_key(link, "test")
        self.make_uncertain()
        self.key.write_text("test_DIFFERENT0123456789")
        with self.assertRaises(InputError):
            lob.reconcile(self.ledger, self.ident, self.key, transport=self.accepted())

    def test_live_needs_fresh_price_explicit_consent_and_allow_live(self):
        live = lob.prepare(self.ledger, self.request_id, ADDRESSES, mode="live", price=self.price(), clock=CLOCK)
        with self.assertRaises(InputError):
            lob.approve(self.ledger, live["id"], live["digest"], reviewed=True, clock=CLOCK)
        self.approve(live["id"])
        with self.assertRaises(InputError):
            lob.send(self.ledger, live["id"], self.live_key, transport=self.accepted(live["id"]), clock=CLOCK)
        with self.assertRaises(InputError):
            lob.send(self.ledger, live["id"], self.live_key, allow_live=True, transport=self.accepted(live["id"]), clock=CLOCK + timedelta(hours=25))
        result = lob.send(self.ledger, live["id"], self.live_key, allow_live=True, transport=self.accepted(live["id"]), clock=CLOCK)
        self.assertIn("Submitted to Lob", result["status"])
        self.assertNotIn("Mailed", result["status"])

    def test_price_basis_rejects_missing_stale_wrong_pages_and_false_totals(self):
        for value in (None, self.price(checked_at=(CLOCK - timedelta(days=2)).isoformat()),
                      self.price(billable_pages=1), self.price(service="certified"), self.price(estimated_total_usd="NaN"),
                      self.price(estimated_total_usd="0"), self.price(source_url="https://example.invalid")):
            with self.assertRaises(InputError):
                lob.prepare(self.ledger, self.request_id, ADDRESSES, mode="live", price=value, clock=CLOCK)

    def test_changed_preview_resets_approval_but_reuses_id(self):
        self.approve()
        changed = copy.deepcopy(ADDRESSES)
        changed["to"]["address_line1"] = "101 Example Postal Way"
        with self.assertRaises(InputError):
            lob.prepare(self.ledger, self.request_id, changed)
        attach_route(self.ledger, self.request_id, dict(self.route, destination="101 Example Postal Way Exampletown FL 00000"))
        updated = lob.prepare(self.ledger, self.request_id, changed, replace=True)
        self.assertEqual(updated["id"], self.ident)
        self.assertEqual(updated["state"], "prepared")
        self.assertNotEqual(updated["digest"], self.job["digest"])
        with self.assertRaises(InputError):
            lob.approve(self.ledger, self.ident, self.job["digest"], reviewed=True)

    def test_old_manual_submission_uncertainty_or_closure_blocks_adapter(self):
        for event in ("submitted", "uncertain", "closed"):
            with self.subTest(event=event), tempfile.TemporaryDirectory() as folder:
                ledger = Ledger(Path(folder) / "private")
                ident = ledger.add_plan(plan(HOUSEHOLD, [piece()]))[0]
                ledger.record(ident, validate_event(event, "user_report", "Fictional external evidence"))
                with self.assertRaises(InputError):
                    lob.prepare(ledger, ident, ADDRESSES)

    def test_external_action_after_approval_blocks_before_http(self):
        self.approve()
        self.ledger.record(self.request_id, validate_event("submitted", "user_report", "Fictional outside send"))
        client = self.accepted()
        with self.assertRaises(InputError):
            lob.send(self.ledger, self.ident, self.key, transport=client)
        self.assertEqual(client.calls, [])

    def test_postmaster_template_is_generic_and_does_not_require_internal_usps_action(self):
        req, observations = office_draft(plan(HOUSEHOLD, EXAMPLES), ["former-letter"], "99 Example Postal Way Exampletown FL 00000")
        ident = self.ledger.add(req, observations)
        local = lob.prepare(self.ledger, ident, ADDRESSES, service="certified")
        self.assertEqual(local["manifest"]["service"], "certified")
        for phrase in ("current occupant", "carrier or delivery supervisor", "delivery records", "not requesting a change of address", "excludes mail addressed to Current Resident"):
            self.assertIn(phrase, req["body"])
        for phrase in ("Postmaster General", "PS Form 3982", "must", "guarantee"):
            self.assertNotIn(phrase, req["body"])
        wrong = copy.deepcopy(ADDRESSES)
        wrong["to"]["address_line1"] = "999 Other Road"
        with self.assertRaises(InputError):
            lob.prepare(self.ledger, ident, wrong, replace=True)

    def test_return_address_and_destination_review_are_required(self):
        for changes in ({"destination_verified": False}, {"from": dict(ADDRESS, address_line2="Unit 9")}):
            with self.assertRaises(InputError):
                lob.prepare(self.ledger, self.request_id, dict(ADDRESSES, **changes), replace=True)

    def test_private_preview_and_schema_migration(self):
        preview = Path(lob.preview(self.ledger, self.ident)["content_pdf"])
        if os.name == "posix":
            self.assertEqual(preview.stat().st_mode & 0o777, 0o600)
        with tempfile.TemporaryDirectory() as folder:
            old = Ledger(Path(folder) / "private")
            ident = old.add_plan(plan(HOUSEHOLD, [piece()]))[0]
            with old.db() as db:
                db.executescript("DROP TABLE routes; DROP TABLE lob_audit; DROP TABLE lob_jobs; PRAGMA user_version=1;")
            reopened = Ledger(old.folder)
            self.assertEqual(reopened.get(ident)["request"]["id"], ident)
            with reopened.db() as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_pdf_unicode_failure_is_explicit(self):
        request = copy.deepcopy(self.ledger.get(self.request_id)["request"])
        request["body"] = "Unsupported example \U0001f600"
        with self.assertRaises(InputError):
            lob.render_letter(request, lob.address(ADDRESS))

    def test_transport_fixed_host_and_redirect_refusal(self):
        with self.assertRaises(InputError):
            lob.Transport().request("GET", "https://example.invalid", "test_FICTIONAL0123456789")
        with self.assertRaises(InputError):
            lob.NoRedirect().redirect_request(None, None, 302, None, None, "https://example.invalid")

    def test_native_receipts_integrate_into_followup_without_manual_submission(self):
        self.approve()
        lob.send(self.ledger, self.ident, self.key, transport=self.accepted())
        self.assertEqual(self.ledger.get(self.request_id)["summary"]["state"], "test_only")
        with self.assertRaises(InputError):
            self.ledger.record(self.request_id, validate_event("acknowledged", "provider_message", "Fictional acknowledgment"))
        live = lob.prepare(self.ledger, self.request_id, ADDRESSES, mode="live", price=self.price(), clock=CLOCK)
        self.approve(live["id"])
        lob.send(self.ledger, live["id"], self.live_key, allow_live=True, transport=self.accepted(live["id"]), clock=CLOCK)
        self.assertEqual(self.ledger.get(self.request_id)["summary"]["state"], "submission_recorded")
        self.assertEqual(self.ledger.get(self.request_id)["events"], [])
        self.ledger.record(self.request_id, validate_event("acknowledged", "provider_message", "Fictional organization acknowledgment"))
        self.ledger.record(self.request_id, validate_event("change_reported", "provider_message", "Fictional organization change report"))
        self.assertEqual(self.ledger.get(self.request_id)["summary"]["state"], "provider_reported_change")
        self.ledger.record(self.request_id, validate_event("closed", "user_report", "Fictional user closure"))
        self.assertEqual(self.ledger.get(self.request_id)["summary"]["state"], "closed_by_user")

    def test_uncertain_live_dominates_test_and_cannot_be_manually_relabelled(self):
        self.approve()
        lob.send(self.ledger, self.ident, self.key, transport=self.accepted())
        live = lob.prepare(self.ledger, self.request_id, ADDRESSES, mode="live", price=self.price(), clock=CLOCK)
        self.approve(live["id"])
        with self.assertRaises(InputError):
            lob.send(self.ledger, live["id"], self.live_key, allow_live=True, transport=FakeTransport(lambda *a, **k: {}), clock=CLOCK)
        self.assertEqual(self.ledger.get(self.request_id)["summary"]["state"], "submission_uncertain")
        for event, source in (("submitted", "provider_message"), ("test_submitted", "local_note"), ("acknowledged", "provider_message")):
            with self.assertRaises(InputError):
                self.ledger.record(self.request_id, validate_event(event, source, "Fictional manual claim"))

    def test_test_uncertainty_never_looks_like_a_real_submission(self):
        self.make_uncertain()
        self.assertEqual(self.ledger.get(self.request_id)["summary"]["state"], "test_submission_uncertain")

    def test_late_transport_failure_cannot_downgrade_reconciled_receipt(self):
        self.approve()
        def reconcile_then_timeout(*a, **k):
            lob.reconcile(self.ledger, self.ident, self.key, transport=FakeTransport(lambda *a, **k: {"data": [self.receipt()]}))
            raise TimeoutError
        result = lob.send(self.ledger, self.ident, self.key, transport=FakeTransport(reconcile_then_timeout))
        self.assertEqual(result["state"], "accepted")

    def test_migration_interruption_rolls_back_tables_and_version(self):
        with tempfile.TemporaryDirectory() as folder:
            old = Ledger(Path(folder) / "private")
            ident = old.add_plan(plan(HOUSEHOLD, [piece()]))[0]
            with old.db() as db:
                db.executescript("DROP TABLE routes; DROP TABLE lob_audit; DROP TABLE lob_jobs; PRAGMA user_version=1;")
            original_connect = sqlite3.connect
            class InterruptedConnection(sqlite3.Connection):
                def executescript(self, script):
                    script = script.replace("CREATE TABLE IF NOT EXISTS lob_audit", "INVALID INTERRUPTION; CREATE TABLE IF NOT EXISTS lob_audit")
                    return super().executescript(script)
            def interrupted_connect(*a, **k):
                return original_connect(*a, **k, factory=InterruptedConnection)
            with patch("mail_stop.ledger.sqlite3.connect", side_effect=interrupted_connect):
                with self.assertRaises(sqlite3.Error):
                    Ledger(old.folder)
            with old.db() as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='lob_jobs'").fetchone())
            self.assertEqual(Ledger(old.folder).get(ident)["request"]["id"], ident)

    def test_actual_transport_serializes_exact_pdf_without_network(self):
        self.approve()
        response_value = self.receipt()
        captured = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(response_value).encode()
        class Opener:
            def open(self, request, timeout):
                captured.append(request)
                return Response()
        with patch("mail_stop.lob.urllib.request.build_opener", return_value=Opener()):
            lob.send(self.ledger, self.ident, self.key)
        request = captured[0]
        self.assertEqual(request.full_url, lob.API)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Idempotency-key"), self.ident)
        self.assertEqual(request.get_header("Lob-version"), "2024-01-01")
        with self.ledger.db() as db:
            document = lob.row_for(db, self.ident)["document"]
        self.assertIn(document, request.data)
        self.assertIn(b'name="metadata[mail_stop_digest]"', request.data)
        self.assertNotIn(b'FICTIONAL0123456789', request.data)

    def test_changed_route_after_approval_blocks_before_http(self):
        self.approve()
        attach_route(self.ledger, self.request_id, dict(self.route, required_steps=["A newly discovered prerequisite"], steps_confirmed=False))
        client = self.accepted()
        with self.assertRaises(InputError):
            lob.send(self.ledger, self.ident, self.key, transport=client)
        self.assertEqual(client.calls, [])

    def test_no_postal_fallback_from_unverified_email_or_enclosure_route(self):
        for changes in ({"verification": "researched"}, {"channel": "email", "destination": "contact@example.invalid"},
                        {"requires_physical_enclosure": True}, {"checked_on": "2020-01-01"}):
            attach_route(self.ledger, self.request_id, dict(self.route, **changes))
            with self.assertRaises(InputError):
                lob.prepare(self.ledger, self.request_id, ADDRESSES, replace=True)

    def make_legacy_preview(self, approved=False):
        with self.ledger.db() as db:
            row = lob.row_for(db, self.ident)
            manifest = json.loads(row["manifest"])
            manifest.pop("route_snapshot")
            checksum = lob.payload_digest(manifest, row["document"])
            approval = json.dumps({"digest": checksum, "reviewed_pdf_addresses_service": True}) if approved else None
            db.execute("UPDATE lob_jobs SET manifest=?,digest=?,state=?,approval=? WHERE id=?",
                       (json.dumps(manifest), checksum, "approved" if approved else "prepared", approval, self.ident))
            db.executescript("DROP TABLE routes; PRAGMA user_version=2;")
        self.ledger = Ledger(self.ledger.folder)
        return checksum

    def test_legacy_prepared_advertising_preview_needs_new_bound_route(self):
        checksum = self.make_legacy_preview()
        with self.assertRaises(InputError):
            lob.approve(self.ledger, self.ident, checksum, reviewed=True)
        attach_route(self.ledger, self.request_id, self.route)
        renewed = lob.prepare(self.ledger, self.request_id, ADDRESSES, replace=True)
        self.assertIsNotNone(renewed["manifest"]["route_snapshot"])
        self.assertNotEqual(renewed["digest"], checksum)
        self.approve()

    def test_legacy_previously_approved_advertising_preview_cannot_bypass_gate(self):
        self.make_legacy_preview(approved=True)
        client = self.accepted()
        with self.assertRaises(InputError):
            lob.send(self.ledger, self.ident, self.key, transport=client)
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
