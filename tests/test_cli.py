import contextlib
import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_stop.cli import main
from test_policy import ROOT, HOUSEHOLD, EXAMPLES


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.data = self.folder / "private"
        self.inputs = ["--household", str(ROOT / "examples/household.json"), "--mail", str(ROOT / "examples/mail.json")]

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_plan_is_read_only_and_network_free(self):
        code, out, err = self.call("plan", *self.inputs)
        self.assertEqual(code, 0, err)
        self.assertIn("unfamiliar-name | unknown | review", out)
        self.assertIn("or-current-resident | current_resident | draft_occupant", out)
        self.assertFalse(self.data.exists())

    def test_complete_local_draft_and_receipt_flow(self):
        code, out, err = self.call("prepare", *self.inputs, "--data", str(self.data))
        self.assertEqual(code, 0, err)
        drafts = json.loads(out)["draft_ids"]
        self.assertEqual(len(drafts), 3)
        ident = drafts[0]
        code, out, err = self.call("export", ident, "--data", str(self.data))
        self.assertEqual(code, 0, err)
        self.assertIn("DRAFT", out)
        self.assertIn("not verified by this kit", out)
        code, out, err = self.call("record", ident, "--data", str(self.data), "--event", "submitted", "--source", "user_report", "--evidence", "Synthetic manual submission")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["summary"]["state"], "submission_recorded")
        code, out, err = self.call("show", ident, "--data", str(self.data))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(json.loads(out)["events"]), 1)

    def test_office_draft_remains_local(self):
        code, out, err = self.call("office-draft", *self.inputs, "--data", str(self.data), "--piece-id", "former-letter", "--office-address", "99 Example Postal Way, Exampletown FL 00000")
        self.assertEqual(code, 0, err)
        response = json.loads(out)
        self.assertFalse(response["submitted"])
        code, out, err = self.call("export", response["draft_id"], "--data", str(self.data))
        self.assertEqual(code, 0, err)
        self.assertIn("Local Postmaster / Delivery Supervisor", out)
        self.assertNotIn("Postmaster General", out)

    def test_invalid_json_does_not_echo_contents(self):
        path = self.folder / "bad.json"
        path.write_text('{"secret": "never-echo-this"')
        code, out, err = self.call("plan", "--household", str(path), "--mail", str(ROOT / "examples/mail.json"))
        self.assertEqual(code, 2)
        self.assertNotIn("never-echo-this", err + out)
        self.assertNotIn("Traceback", err)

    def test_malformed_types_are_a_controlled_error(self):
        path = self.folder / "bad.json"
        path.write_text(json.dumps([dict(EXAMPLES[0], kind=[])]))
        code, out, err = self.call("plan", "--household", str(ROOT / "examples/household.json"), "--mail", str(path))
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)

    def test_packaged_playbooks_have_dated_primary_sources(self):
        code, out, err = self.call("playbooks")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["checked_on"], "2026-10-19")
        self.assertGreaterEqual(len(data["playbooks"]), 5)
        for entry in data["playbooks"]:
            self.assertTrue(entry["sources"])
            self.assertTrue(all(url.startswith("https://") for url in entry["sources"]))

    @unittest.skipUnless(importlib.util.find_spec("reportlab"), "Install .[lob] for PDF CLI tests")
    def test_lob_prepare_preview_approve_status_are_local(self):
        code, out, err = self.call("office-draft", *self.inputs, "--data", str(self.data), "--piece-id", "former-letter",
                                   "--office-address", "99 Example Postal Way, Exampletown FL 00000")
        self.assertEqual(code, 0, err)
        draft = json.loads(out)["draft_id"]
        code, out, err = self.call("lob-prepare", draft, "--addresses", str(ROOT / "examples/lob-addresses.json"), "--data", str(self.data))
        self.assertEqual(code, 0, err)
        job = json.loads(out)
        self.assertEqual(job["mode"], "test")
        self.assertTrue(Path(job["content_pdf"]).read_bytes().startswith(b"%PDF-"))
        code, out, err = self.call("lob-approve", job["id"], "--digest", job["digest"], "--reviewed-pdf-addresses-service", "--data", str(self.data))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["state"], "approved")
        code, out, err = self.call("lob-status", job["id"], "--data", str(self.data))
        self.assertEqual(code, 0, err)
        self.assertIn("nothing submitted", json.loads(out)["status"])
        code, out, err = self.call("list", "--data", str(self.data))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)[0]["lob_mailings"][0]["id"], job["id"])

    def test_route_directory_attachment_and_agenda_stay_local(self):
        selected = self.folder / "selected.json"
        selected.write_text(json.dumps([dict(EXAMPLES[0], sender="Valpak")]))
        code, out, err = self.call("prepare", "--household", str(ROOT / "examples/household.json"), "--mail", str(selected), "--data", str(self.data))
        self.assertEqual(code, 0, err)
        ident = json.loads(out)["draft_ids"][0]
        code, out, err = self.call("routes", "--id", "valpak")
        self.assertEqual(code, 0, err)
        route = json.loads(out)
        route_path = self.folder / "route.json"
        route_path.write_text(json.dumps(route))
        code, out, err = self.call("attach-route", ident, "--route", str(route_path), "--data", str(self.data))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["next_step"]["next"], "verify_route")
        code, out, err = self.call("agenda", "--id", ident, "--data", str(self.data), "--on", "2026-10-19")
        self.assertEqual(code, 0, err)
        self.assertFalse(json.loads(out)[0]["automatic_action"])

    @unittest.skipUnless(importlib.util.find_spec("reportlab"), "Install .[lob] for household PDF CLI tests")
    def test_household_kit_cli_prepares_only_for_current_household(self):
        code, out, err = self.call("household-kit", "--household", str(ROOT / "examples/household.json"), "--recipient", "Alex Example",
                                   "--updated-on", "2026-10-19", "--data", str(self.data))
        self.assertEqual(code, 0, err)
        ident = json.loads(out)["draft_id"]
        addresses = json.loads((ROOT / "examples/lob-addresses.json").read_text())
        addresses["to"] = addresses["from"]
        path = self.folder / "home-addresses.json"
        path.write_text(json.dumps(addresses))
        code, out, err = self.call("lob-prepare", ident, "--addresses", str(path), "--data", str(self.data))
        self.assertEqual(code, 0, err)
        job = json.loads(out)
        self.assertEqual(job["manifest"]["content_pages"], 1)
        self.assertEqual(job["manifest"]["service"], "none")
        self.assertEqual(job["manifest"]["to"]["name"], "Alex Example")
        code, out, err = self.call("next", "--id", ident, "--data", str(self.data))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)[0]["next"], "ready_for_review")


if __name__ == "__main__":
    unittest.main()
