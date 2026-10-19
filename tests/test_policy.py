import copy
import json
import unittest
from pathlib import Path

from mail_stop.policy import InputError, address_key, office_draft, plan

ROOT = Path(__file__).resolve().parents[1]
HOUSEHOLD = json.loads((ROOT / "examples/household.json").read_text())
EXAMPLES = json.loads((ROOT / "examples/mail.json").read_text())


def piece(**changes):
    value = copy.deepcopy(EXAMPLES[0])
    value.update(changes)
    return value


def row(value, household=None):
    return plan(household or HOUSEHOLD, [value])["pieces"][0]


class RoutingTests(unittest.TestCase):
    def test_demo_separates_all_six_cases(self):
        result = plan(HOUSEHOLD, EXAMPLES)
        self.assertEqual([r["decision"]["route"] for r in result["pieces"]],
                         ["draft_household", "draft_former_resident", "draft_occupant", "review", "review", "return_unopened"])
        self.assertEqual(sum(r["request"] is not None for r in result["pieces"]), 3)

    def test_unfamiliar_ad_does_not_assert_former_residency(self):
        result = row(piece(recipient="Unknown Example"))
        self.assertEqual(result["decision"]["recipient_role"], "unknown")
        self.assertIsNone(result["request"])

    def test_current_resident_wins_even_when_reader_flag_is_false(self):
        for name in ("Taylor Former or Current Resident", "Current Occupant or Taylor Former", "Resident", "Householder", "Postal Customer"):
            with self.subTest(name=name):
                result = row(piece(recipient=name, current_resident=False))
                self.assertEqual(result["decision"]["recipient_role"], "current_resident")
                self.assertEqual(result["request"]["kind"], "occupant")
                self.assertNotIn("Taylor Former", result["request"]["body"])

    def test_person_named_resident_is_not_a_generic_occupant(self):
        self.assertEqual(row(piece(recipient="Resident Smith"))["decision"]["recipient_role"], "unknown")

    def test_review_is_required_even_for_known_recipient(self):
        for fields in ([], ["recipient"], ["address"], ["recipient", "address"]):
            with self.subTest(fields=fields):
                self.assertIsNone(row(piece(reviewed_fields=fields))["request"])

    def test_conflicting_household_names_rejected(self):
        h = dict(HOUSEHOLD, former_residents=["Alex Example"])
        with self.assertRaises(InputError):
            plan(h, [piece()])

    def test_units_are_never_guessed(self):
        for address in ("100 Example Lane\nExampletown, FL 00000", "100 Example Lane Unit 5\nExampletown, FL 00000"):
            with self.subTest(address=address):
                result = row(piece(address=address))
                self.assertEqual(result["decision"]["route"], "review")
                self.assertIsNone(result["request"])

    def test_address_spelling_and_zip_extension_only(self):
        self.assertEqual(address_key("100 Example Lane Apt 4 Exampletown FL 00000-1234"), address_key(HOUSEHOLD["address"]))
        self.assertNotEqual(address_key("100 Example Lane Unit 14 Exampletown FL 00000"), address_key(HOUSEHOLD["address"]))

    def test_protected_visible_cue_overrides_ad_label(self):
        for cue in ("Explanation of Benefits", "Payment Due", "Jury Summons", "Tax Notice"):
            with self.subTest(cue=cue):
                result = row(piece(evidence=cue))
                self.assertTrue(result["decision"]["protected"])
                self.assertIsNone(result["request"])

    def test_former_resident_important_mail_returns_without_account_action(self):
        result = row(piece(recipient="Taylor Former", evidence="Payment due"))
        self.assertEqual(result["decision"]["route"], "return_unopened")
        self.assertIsNone(result["request"])

    def test_named_prescreen_offer_is_self_service(self):
        result = row(piece(prescreen_notice=True))
        self.assertEqual(result["decision"]["route"], "self_service_prescreen")
        self.assertIsNone(result["request"])

    def test_former_prescreen_offer_does_not_change_credit_preferences(self):
        result = row(piece(recipient="Taylor Former", prescreen_notice=True))
        self.assertEqual(result["request"]["kind"], "former_resident")
        self.assertIn("not on that person's behalf", result["request"]["body"])

    def test_unknown_or_personal_mail_never_gets_advertising_template(self):
        for kind in ("unknown", "personal", "potentially_important"):
            self.assertIsNone(row(piece(kind=kind))["request"])

    def test_forwarded_or_lookalike_mail_stays_for_review(self):
        for change in ({"forwarded": True}, {"lookalike": True}):
            self.assertIsNone(row(piece(**change))["request"])

    def test_nonmail_is_ignored(self):
        self.assertEqual(row(piece(is_mailpiece=False))["decision"]["route"], "ignore_nonmail")

    def test_sender_specific_draft_preserves_required_notices(self):
        result = row(piece())["request"]
        self.assertIn("not account statements or required notices", result["body"])
        self.assertIsNone(result["destination"])

    def test_stable_target_id_does_not_depend_on_scan_id(self):
        self.assertEqual(row(piece())["request"]["id"], row(piece(id="another-scan"))["request"]["id"])

    def test_mutable_defaults_are_not_shared(self):
        a = row(piece())["piece"]
        a["endorsements"].append("Return Service Requested")
        self.assertEqual(row(piece())["piece"]["endorsements"], [])


class ValidationTests(unittest.TestCase):
    def test_invalid_values_fail_without_echoing_private_content(self):
        for change in ({"kind": []}, {"mail_class": {}}, {"current_resident": "false"}, {"reviewed_fields": "recipient"},
                       {"recipient": "secret\x1b[2J"}, {"received_on": "2026-02-30"}, {"received_on": 123},
                       {"endorsements": ["Made Up Endorsement"]}, {"extra_private_field": "never echo this"}):
            with self.subTest(change=change):
                with self.assertRaises(InputError) as error:
                    row(piece(**change))
                self.assertNotIn("never echo", str(error.exception))
                self.assertNotIn("secret", str(error.exception))

    def test_duplicate_piece_ids_rejected(self):
        with self.assertRaises(InputError):
            plan(HOUSEHOLD, [piece(), piece()])

    def test_empty_normalized_household_name_rejected(self):
        with self.assertRaises(InputError):
            plan(dict(HOUSEHOLD, residents=["..."]), [])

    def test_oversized_batches_rejected(self):
        with self.assertRaises(InputError):
            plan(HOUSEHOLD, [piece()] * 1001)


class OfficeTests(unittest.TestCase):
    def test_office_draft_uses_only_selected_confirmed_evidence(self):
        p = plan(HOUSEHOLD, EXAMPLES)
        request, observations = office_draft(p, ["former-letter"], "99 Example Postal Way, Exampletown FL 00000")
        self.assertEqual(len(observations), 1)
        self.assertIn("2026-10-06", request["body"])
        self.assertNotIn("Jamie Unknown", request["body"])
        self.assertNotIn("PS Form 3982", request["body"])
        self.assertIn("not requesting a change of address", request["body"])

    def test_occupant_unknown_missing_or_duplicate_selection_refused(self):
        p = plan(HOUSEHOLD, EXAMPLES)
        for ids in ([], ["or-current-resident"], ["unfamiliar-name"], ["missing"], ["former-letter", "former-letter"]):
            with self.subTest(ids=ids):
                with self.assertRaises(InputError):
                    office_draft(p, ids, "99 Example Postal Way")

    def test_unreviewed_or_wrong_address_cannot_enter_office_letter(self):
        for changes in ({"reviewed_fields": []}, {"address": "99 Another Street"}):
            value = piece(recipient="Taylor Former", **changes)
            p = plan(HOUSEHOLD, [value])
            with self.assertRaises(InputError):
                office_draft(p, [value["id"]], "99 Example Postal Way")


if __name__ == "__main__":
    unittest.main()
