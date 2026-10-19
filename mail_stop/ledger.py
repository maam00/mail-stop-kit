"""Private, append-only observations and outcomes; this module never submits mail."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .policy import InputError, text

EVENTS = {"submitted", "uncertain", "acknowledged", "change_reported", "mail_observed", "test_submitted", "closed", "confirmation_completed"}
SOURCES = {"user_report", "provider_message", "selected_envelope", "selected_digest", "local_note"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_event(kind, source, evidence, observed_on=None, reference=None):
    if kind not in EVENTS or source not in SOURCES:
        raise InputError("Unsupported event or evidence source.")
    if kind in {"acknowledged", "change_reported"} and source != "provider_message":
        raise InputError("Acknowledgment or a provider-reported change requires a provider message; a user report is not a provider receipt.")
    allowed = {"submitted": {"user_report", "provider_message"}, "uncertain": {"user_report", "local_note"},
               "test_submitted": {"provider_message", "local_note"}, "closed": {"user_report"},
               "confirmation_completed": {"user_report", "provider_message"},
               "mail_observed": {"user_report", "selected_envelope", "selected_digest"}}
    if kind in allowed and source not in allowed[kind]:
        raise InputError("That evidence source is not suitable for this event.")
    result = {"kind": kind, "source": source, "evidence": text(evidence, "Evidence note", 2000)}
    if kind == "mail_observed" and observed_on is None:
        raise InputError("A mail observation needs --observed-on YYYY-MM-DD.")
    if observed_on is not None:
        from datetime import date
        try:
            if not isinstance(observed_on, str) or date.fromisoformat(observed_on).isoformat() != observed_on:
                raise ValueError
        except ValueError:
            raise InputError("observed_on must be an ISO date (YYYY-MM-DD).") from None
        result["observed_on"] = observed_on
    if reference is not None:
        result["reference"] = text(reference, "Receipt reference", 500)
    return result


def summary(events, mailings=()):
    state, latest_observation = "draft", None
    labels = {"submitted": "submission_recorded", "uncertain": "submission_uncertain", "acknowledged": "provider_acknowledged",
              "change_reported": "provider_reported_change", "test_submitted": "test_only", "closed": "closed_by_user"}
    for event in events:
        if event["kind"] == "confirmation_completed":
            continue
        if event["kind"] == "mail_observed":
            if latest_observation is None or event["observed_on"] >= latest_observation["observed_on"]:
                latest_observation = event
        else:
            state = labels[event["kind"]]
    if state not in {"closed_by_user", "provider_acknowledged", "provider_reported_change"}:
        real = [m for m in mailings if m["mode"] == "live"]
        tests = [m for m in mailings if m["mode"] == "test"]
        if any(m["state"] == "accepted" for m in real):
            state = "submission_recorded"
        elif any(m["state"] in {"submitting", "uncertain"} for m in real) and state != "submission_recorded":
            state = "submission_uncertain"
        elif state == "draft":
            if any(m["state"] == "accepted" for m in tests):
                state = "test_only"
            elif any(m["state"] in {"submitting", "uncertain"} for m in tests):
                state = "test_submission_uncertain"
    return {"state": state, "latest_mail_observation": latest_observation,
            "mail_reduction_verified": False,
            "note": "Manual events are operator-supplied. Lob evidence is shown separately and can establish provider acceptance, never that mail stopped."}


class Ledger:
    def __init__(self, folder):
        self.folder = Path(folder).expanduser().absolute()
        if self.folder.is_symlink():
            raise InputError("Use a dedicated directory, not a symlink, for private data.")
        self.path = self.folder / "mail-stop.sqlite3"
        if self.folder.exists():
            if not self.folder.is_dir():
                raise InputError("The data path must be a dedicated directory.")
            if not self.path.exists() and any(self.folder.iterdir()):
                raise InputError("Choose an empty dedicated data directory; existing unrelated files will not be changed.")
        else:
            self.folder.mkdir(mode=0o700, parents=True)
        if self.path.is_symlink():
            raise InputError("The private database must not be a symlink.")
        if self.path.exists():
            info = self.path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise InputError("The private database must be a regular file with one link.")
        else:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        with self.db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version not in {0, 1, 2, 3} or (version == 0 and existing):
                raise InputError("This is not a supported mail-stop ledger.")
            if version in {1, 2, 3} and existing != ({"requests", "observations", "events", "sqlite_sequence"} | ({"lob_jobs", "lob_audit"} if version >= 2 else set()) | ({"routes"} if version == 3 else set())):
                raise InputError("This is not a supported mail-stop ledger.")
            expected_columns = {
                "requests": [("id", "TEXT", 0, 1), ("payload", "TEXT", 1, 0), ("created_at", "TEXT", 1, 0)],
                "observations": [("hash", "TEXT", 0, 1), ("request_id", "TEXT", 1, 0), ("payload", "TEXT", 1, 0), ("created_at", "TEXT", 1, 0)],
                "events": [("sequence", "INTEGER", 0, 1), ("hash", "TEXT", 1, 0), ("request_id", "TEXT", 1, 0), ("payload", "TEXT", 1, 0), ("created_at", "TEXT", 1, 0)],
            }
            if version >= 2:
                expected_columns.update({
                    "lob_jobs": [("id", "TEXT", 0, 1), ("request_id", "TEXT", 1, 0), ("mode", "TEXT", 1, 0),
                                 ("document", "BLOB", 1, 0), ("manifest", "TEXT", 1, 0), ("digest", "TEXT", 1, 0),
                                 ("state", "TEXT", 1, 0), ("approval", "TEXT", 0, 0), ("claimed_at", "TEXT", 0, 0),
                                 ("key_hash", "TEXT", 0, 0), ("receipt", "TEXT", 0, 0), ("created_at", "TEXT", 1, 0)],
                    "lob_audit": [("sequence", "INTEGER", 0, 1), ("job_id", "TEXT", 1, 0),
                                  ("payload", "TEXT", 1, 0), ("created_at", "TEXT", 1, 0)],
                })
            if version == 3:
                expected_columns["routes"] = [("sequence", "INTEGER", 0, 1), ("hash", "TEXT", 1, 0),
                                               ("request_id", "TEXT", 1, 0), ("payload", "TEXT", 1, 0), ("created_at", "TEXT", 1, 0)]
            if version in {1, 2, 3}:
                for table, expected in expected_columns.items():
                    actual = [(r["name"], r["type"], r["notnull"], r["pk"]) for r in db.execute(f"PRAGMA table_info({table})")]
                    if actual != expected:
                        raise InputError("This is not a supported mail-stop ledger schema.")
            os.chmod(self.folder, 0o700)
            os.chmod(self.path, 0o600)
            db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations (
                    hash TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
                    payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    hash TEXT UNIQUE NOT NULL, request_id TEXT NOT NULL REFERENCES requests(id),
                    payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS lob_jobs (
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id), mode TEXT NOT NULL,
                    document BLOB NOT NULL, manifest TEXT NOT NULL, digest TEXT NOT NULL, state TEXT NOT NULL,
                    approval TEXT, claimed_at TEXT, key_hash TEXT, receipt TEXT, created_at TEXT NOT NULL,
                    UNIQUE(request_id, mode));
                CREATE TABLE IF NOT EXISTS lob_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES lob_jobs(id),
                    payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS routes (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT UNIQUE NOT NULL,
                    request_id TEXT NOT NULL REFERENCES requests(id), payload TEXT NOT NULL, created_at TEXT NOT NULL);
                PRAGMA user_version=3;
                COMMIT;
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _add(db, request, observations):
        payload = canonical(request)
        row = db.execute("SELECT payload FROM requests WHERE id=?", (request["id"],)).fetchone()
        if row and row["payload"] != payload:
            raise InputError("A request for this target already has a different draft. Review its history before changing the scope; it was not overwritten.")
        db.execute("INSERT OR IGNORE INTO requests(id,payload,created_at) VALUES(?,?,?)", (request["id"], payload, now()))
        for observation in observations:
            encoded = canonical(observation)
            digest = hashlib.sha256((request["id"] + encoded).encode()).hexdigest()
            db.execute("INSERT OR IGNORE INTO observations(hash,request_id,payload,created_at) VALUES(?,?,?,?)",
                       (digest, request["id"], encoded, now()))
        return request["id"]

    def add(self, request, observations):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._add(db, request, observations)

    def add_plan(self, plan_value):
        ids = []
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in plan_value["pieces"]:
                if row["request"]:
                    ids.append(self._add(db, row["request"], [row["piece"]]))
        return sorted(set(ids))

    @staticmethod
    def _events(db, ident):
        return [dict(json.loads(row["payload"]), recorded_at=row["created_at"])
                for row in db.execute("SELECT payload,created_at FROM events WHERE request_id=? ORDER BY sequence", (ident,))]

    def get(self, ident):
        with self.db() as db:
            row = db.execute("SELECT payload,created_at FROM requests WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise InputError("No request with that ID exists in this ledger.")
            events = self._events(db, ident)
            observations = [json.loads(item[0]) for item in db.execute("SELECT payload FROM observations WHERE request_id=? ORDER BY created_at,hash", (ident,))]
            mailings = [dict(r) for r in db.execute(
                "SELECT id,mode,state,claimed_at FROM lob_jobs WHERE request_id=? ORDER BY created_at", (ident,))]
            return {"request": json.loads(row["payload"]), "created_at": row["created_at"], "events": events,
                    "observations": observations, "summary": summary(events, mailings), "lob_mailings": mailings,
                    "routes": [dict(json.loads(r["payload"]), recorded_at=r["created_at"]) for r in db.execute(
                        "SELECT payload,created_at FROM routes WHERE request_id=? ORDER BY sequence", (ident,))]}

    def list(self):
        with self.db() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM requests ORDER BY created_at,id")]
        return [self.get(ident) for ident in ids]

    def record(self, ident, event):
        # Revalidate for Python callers as well as the CLI.
        event = validate_event(event.get("kind"), event.get("source"), event.get("evidence"),
                               event.get("observed_on"), event.get("reference"))
        encoded = canonical(event)
        digest = hashlib.sha256((ident + encoded).encode()).hexdigest()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM requests WHERE id=?", (ident,)).fetchone() is None:
                raise InputError("No request with that ID exists in this ledger.")
            if db.execute("SELECT 1 FROM events WHERE hash=?", (digest,)).fetchone():
                return False  # Replaying the same local evidence does not create another event.
            events = self._events(db, ident)
            prior = {item["kind"] for item in events}
            mailings = [dict(r) for r in db.execute("SELECT mode,state FROM lob_jobs WHERE request_id=?", (ident,))]
            real_accepted = any(m["mode"] == "live" and m["state"] == "accepted" for m in mailings)
            native_claimed = any(m["state"] in {"submitting", "uncertain", "accepted"} for m in mailings)
            state = summary(events, mailings)["state"]
            if state == "closed_by_user" and event["kind"] != "mail_observed":
                raise InputError("This request was closed by the user; it will not be silently reopened.")
            if event["kind"] in {"acknowledged", "change_reported", "confirmation_completed"} and "submitted" not in prior and not real_accepted:
                raise InputError("Record evidence of a real submission first. Test-only or uncertain submission is insufficient.")
            if event["kind"] in {"test_submitted", "uncertain", "submitted"} and native_claimed:
                raise InputError("A native Lob attempt already has its own evidence. Use lob-status/lob-reconcile instead of relabeling it manually.")
            if event["kind"] in {"test_submitted", "uncertain", "submitted"} and "submitted" in prior:
                raise InputError("A submission is already recorded. Reconcile the existing receipt instead of recording a resend.")
            if event["kind"] == "test_submitted" and "uncertain" in prior:
                raise InputError("A potentially real submission is uncertain; reconcile it before further attempts.")
            if event["kind"] == "acknowledged" and "change_reported" in prior:
                raise InputError("A change report already exists; preserve that outcome instead of downgrading it.")
            db.execute("INSERT INTO events(hash,request_id,payload,created_at) VALUES(?,?,?,?)", (digest, ident, encoded, now()))
        return True
