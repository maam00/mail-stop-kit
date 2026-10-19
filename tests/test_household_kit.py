import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_stop import lob
from mail_stop.ledger import Ledger
from mail_stop.policy import InputError, household_kit, office_draft, plan
from mail_stop.routes import next_step
from test_policy import HOUSEHOLD, EXAMPLES
from test_lob import ADDRESS, ADDRESSES, FakeTransport


class HouseholdKitPolicyTests(unittest.TestCase):
    def test_recipient_must_be_confirmed_current_not_former_unknown_or_occupant(self):
        for name in ("Taylor Former", "Unknown Example", "Current Resident"):
            with self.assertRaises(InputError):
                household_kit(HOUSEHOLD, name, "2026-10-19")
        self.assertEqual(household_kit(HOUSEHOLD, "Alex Example", "2026-10-19")["kind"], "household_kit")

    def test_date_and_current_recipient_roster_are_not_inferred(self):
        with self.assertRaises(InputError):
            household_kit(HOUSEHOLD, "Alex Example", "not-a-date")
        request = household_kit(HOUSEHOLD, "Alex Example", "2026-10-19")
        self.assertEqual(request["kit_residents"], HOUSEHOLD["residents"])
        self.assertEqual(request["kit_former_residents"], HOUSEHOLD["former_residents"])
        self.assertEqual(request["destination"]["address"], HOUSEHOLD["address"])

    def test_same_household_does_not_get_new_id_by_changing_recipient_or_date(self):
        a = household_kit(HOUSEHOLD, "Alex Example", "2026-10-19")
        b = household_kit(HOUSEHOLD, "Sam Example", "2026-10-20")
        self.assertEqual(a["id"], b["id"])
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(Path(folder) / "private")
            ledger.add(a, [])
            with self.assertRaises(InputError):
                ledger.add(b, [])


@unittest.skipUnless(importlib.util.find_spec("reportlab"), "Install .[lob] for household PDF tests")
class HouseholdKitPdfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "private")
        self.request = household_kit(HOUSEHOLD, "Alex Example", "2026-10-19")
        self.ident = self.ledger.add(self.request, [])
        self.addresses = {"from": ADDRESS, "to": ADDRESS, "destination_verified": True}

    def tearDown(self):
        self.temp.cleanup()

    def test_two_distinct_cutouts_on_one_sheet_with_former_names_only_on_carrier_copy(self):
        from reportlab.platypus import Paragraph
        with patch("reportlab.platypus.Paragraph", wraps=Paragraph) as paragraphs:
            pdf, pages = lob.render_letter(self.request, lob.address(ADDRESS))
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertEqual(pages, 1)
        texts = [call.args[0] for call in paragraphs.call_args_list]
        split = texts.index("CARRIER COPY")
        self.assertIn("MAILBOX NAME GUIDE", texts[:split])
        self.assertNotIn("Taylor Former", " ".join(texts[:split]))
        self.assertIn("Taylor Former", " ".join(texts[split:]))
        self.assertEqual(sum("Current Resident or Occupant mail remains valid" in t for t in texts), 2)
        self.assertIn("not a USPS form or a change-of-address request", " ".join(texts))
        self.assertNotIn("only these names", " ".join(texts))

    def test_large_roster_refused_instead_of_clipped_or_shrunk(self):
        household = copy.deepcopy(HOUSEHOLD)
        household["residents"] = ["Alex Example"] + [f"Long Fictional Household Recipient {i} with an Extended Name" for i in range(30)]
        request = household_kit(household, "Alex Example", "2026-10-19")
        with self.assertRaises(InputError) as error:
            lob.render_letter(request, lob.address(ADDRESS))
        self.assertIn("fit legibly", str(error.exception))

    def test_kit_goes_to_household_in_one_ordinary_letter_with_same_safeguards(self):
        job = lob.prepare(self.ledger, self.ident, self.addresses)
        self.assertEqual(job["manifest"]["service"], "none")
        self.assertEqual(job["manifest"]["content_pages"], 1)
        self.assertEqual(job["manifest"]["billable_pages"], 2)
        self.assertEqual(job["manifest"]["to"]["name"], "Alex Example")
        self.assertIsNone(job["manifest"]["route_snapshot"])
        lob.approve(self.ledger, job["id"], job["digest"], reviewed=True)
        key = Path(self.temp.name) / "key"
        key.write_text("test_FICTIONAL0123456789")
        key.chmod(0o600)
        response = {"id": "ltr_FICTIONALKIT", "object": "letter", "metadata": {"mail_stop_id": job["id"], "mail_stop_digest": job["digest"]},
                    "from": job["manifest"]["from"], "to": job["manifest"]["to"]}
        client = FakeTransport(lambda *a, **k: response)
        result = lob.send(self.ledger, job["id"], key, transport=client)
        self.assertEqual(result["state"], "accepted")
        self.assertEqual(len(client.calls), 1)
        self.assertNotIn("extra_service", client.calls[0][2]["fields"])
        with self.assertRaises(InputError):
            lob.send(self.ledger, job["id"], key, transport=client)
        self.assertEqual(next_step(self.ledger.get(self.ident))["next"], "ready_for_review")  # Test did not deliver a kit.

    def test_kit_cannot_redirect_to_office_former_recipient_or_certified(self):
        for addresses in (ADDRESSES, dict(self.addresses, to=dict(ADDRESS, name="Taylor Former")),
                          {**self.addresses, "from": dict(ADDRESS, name="Taylor Former")},
                          dict(self.addresses, to=dict(ADDRESS, address_line2="Unit 9"))):
            with self.assertRaises(InputError):
                lob.prepare(self.ledger, self.ident, addresses)
        with self.assertRaises(InputError):
            lob.prepare(self.ledger, self.ident, self.addresses, service="certified")

    def test_office_letter_still_cannot_be_redirected_to_household(self):
        request, observations = office_draft(plan(HOUSEHOLD, EXAMPLES), ["former-letter"], "99 Example Postal Way Exampletown FL 00000")
        ident = self.ledger.add(request, observations)
        with self.assertRaises(InputError):
            lob.prepare(self.ledger, ident, self.addresses)


if __name__ == "__main__":
    unittest.main()
