"""Explicit, single-attempt Lob letters. No automatic retries or key discovery.

Provider I/O is injectable for offline tests. The CLI uses only the fixed Lob
HTTPS endpoint. A durable claim is committed before the first HTTP byte.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .ledger import canonical, now
from .letter_pdf import render_letter
from .policy import InputError, address_key, exact_keys, normalize, text
from .routes import postal_route

API = "https://api.lob.com/v1/letters"
VERSION = "2024-01-01"
SERVICES = {"none", "certified", "certified_return_receipt"}
STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())
MAX_RESPONSE = 2_000_000
LOCKED = {"submitting", "uncertain", "accepted"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise InputError("Use an ISO timestamp with a timezone for the price check.") from None


def address(value):
    required = {"name", "address_line1", "address_city", "address_state", "address_zip"}
    exact_keys(value, required | {"address_line2", "address_country"}, "Postal address")
    if not required <= set(value):
        raise InputError("Postal addresses need name, street, city, state and ZIP.")
    out = {key: text(value[key], "Postal address field", 150) for key in required}
    if any("\n" in v or "\t" in v for v in out.values()):
        raise InputError("Structured postal fields must each be a single line.")
    out["address_line2"] = text(value.get("address_line2", ""), "Address line 2", 150, allow_empty=True)
    if "\n" in out["address_line2"] or "\t" in out["address_line2"]:
        raise InputError("Address line 2 must be a single line.")
    out["address_state"] = out["address_state"].upper()
    if out["address_state"] not in STATES or not re.fullmatch(r"\d{5}(?:-\d{4})?", out["address_zip"]):
        raise InputError("This adapter supports US states/DC and five-digit or ZIP+4 postal codes only.")
    if value.get("address_country", "US") != "US":
        raise InputError("Only domestic US letters are supported.")
    out["address_country"] = "US"
    return out


def address_text(value):
    return " ".join(value[key] for key in ("address_line1", "address_line2", "address_city", "address_state", "address_zip") if value[key])


def price_basis(value, mode, pages, service, clock):
    if mode == "test":
        if value is not None:
            raise InputError("Test mode has no paid price basis; omit --price.")
        return {"estimated_total_usd": "0.00", "basis": "Lob test mode: no physical mail or charges."}
    required = {"estimated_total_usd", "checked_at", "source_url", "account_plan", "content_pages", "billable_pages", "service", "assumptions"}
    exact_keys(value, required, "Price basis")
    if set(value) != required:
        raise InputError("Live mode needs every documented account-specific price-basis field.")
    try:
        if not isinstance(value["estimated_total_usd"], str):
            raise InvalidOperation
        amount = Decimal(value["estimated_total_usd"])
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise InvalidOperation
    except InvalidOperation:
        raise InputError("The live estimate must be a positive USD amount with at most two decimal places.") from None
    checked = timestamp(value["checked_at"])
    if not timedelta(0) <= clock - checked <= timedelta(hours=24):
        raise InputError("Refresh the applicable account price basis; its check must be within the last 24 hours, not in the future.")
    url = urllib.parse.urlparse(value["source_url"] if isinstance(value["source_url"], str) else "")
    if url.scheme != "https" or url.hostname not in {"lob.com", "www.lob.com", "dashboard.lob.com", "help.lob.com"} or url.username or url.password or url.query or url.fragment:
        raise InputError("Use a public Lob pricing/billing page or your dashboard page URL without query parameters or credentials.")
    if type(value["content_pages"]) is not int or type(value["billable_pages"]) is not int or value["content_pages"] != pages or value["billable_pages"] != pages + 1 or value["service"] != service:
        raise InputError("The price basis must match the content pages, added billable address page and selected service.")
    return {**value, "estimated_total_usd": format(amount, ".2f"),
            "account_plan": text(value["account_plan"], "Account plan", 200),
            "assumptions": text(value["assumptions"], "Pricing assumptions", 1500)}


def payload_digest(manifest, document):
    return digest(canonical({"manifest": manifest, "pdf_sha256": digest(document)}).encode())


def audit(db, ident, event, **details):
    db.execute("INSERT INTO lob_audit(job_id,payload,created_at) VALUES(?,?,?)",
               (ident, canonical({"event": event, **details}), now()))


def row_for(db, ident):
    row = db.execute("SELECT * FROM lob_jobs WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise InputError("No Lob mailing with that ID exists in this ledger.")
    return row


def decoded(row):
    return json.loads(row["manifest"])


def integrity(row):
    manifest = decoded(row)
    if row["digest"] != payload_digest(manifest, row["document"]) or manifest["mode"] != row["mode"]:
        raise InputError("The saved PDF/manifest does not match its approval digest. Do not send it.")
    return manifest


def eligible(db, request_id):
    if db.execute("SELECT 1 FROM lob_jobs WHERE request_id=? AND mode='live' AND claimed_at IS NOT NULL", (request_id,)).fetchone():
        raise InputError("A real Lob submission is already claimed for this request. Reconcile it instead of starting another attempt.")
    events = [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE request_id=? ORDER BY sequence", (request_id,))]
    if any(e["kind"] in {"submitted", "uncertain", "closed"} for e in events):
        raise InputError("This request has an outside submission, uncertainty or closure. Reconcile that history instead of starting another send.")


def prepare(ledger, request_id, addresses, *, mode="test", service="none", price=None, replace=False, clock=None):
    if mode not in {"test", "live"} or service not in SERVICES:
        raise InputError("Choose test/live mode and a supported letter service.")
    exact_keys(addresses, {"from", "to", "destination_verified"}, "Mailing addresses")
    if addresses.get("destination_verified") is not True:
        raise InputError("Verify the organization/local office's current mailing address before preparing a mailing.")
    sender, recipient = address(addresses.get("from")), address(addresses.get("to"))
    entry = ledger.get(request_id)
    request = entry["request"]
    if request["kind"] == "household_kit":
        if normalize(sender["name"]) not in {normalize(n) for n in request["kit_residents"]}:
            raise InputError("The kit return name must be a confirmed current resident, not a former recipient.")
        if service != "none":
            raise InputError("The household kit uses an ordinary letter, not certified service.")
        if normalize(recipient["name"]) != normalize(request["destination"]["name"]):
            raise InputError("The kit must be addressed to the selected confirmed current resident.")
    if address_key(address_text(sender)) != address_key(request["address"]):
        raise InputError("The return address must match this request's confirmed household address.")
    if request["destination"] and address_key(address_text(recipient)) != address_key(request["destination"]["address"]):
        raise InputError("The postal destination differs from this draft's confirmed destination address.")
    route_snapshot = None
    if request["kind"] in {"household", "occupant", "former_resident"}:
        with ledger.db() as db:
            route_snapshot = postal_route(db, request_id, (clock or datetime.now(timezone.utc)).date())
        if address_key(address_text(recipient)) != address_key(route_snapshot["destination"].removesuffix("USA").removesuffix("US")):
            raise InputError("The letter destination differs from the verified postal contact route.")
    document, pages = render_letter(request, sender)
    clock = clock or datetime.now(timezone.utc)
    manifest = {"schema_version": 1, "request_id": request_id, "mode": mode, "route_snapshot": route_snapshot, "from": sender, "to": recipient,
                "service": service, "content_pages": pages, "billable_pages": pages + 1,
                "pdf_sha256": digest(document), "price": price_basis(price, mode, pages, service, clock),
                "options": {"color": False, "double_sided": False, "mail_type": "usps_first_class", "use_type": "operational",
                            "size": "us_letter", "address_placement": "insert_blank_page"},
                "preview_note": "Exact uploaded content PDF. Lob adds a billable address page and, for certified service, barcode/address material. This is not Lob's final rendered envelope proof.",
                "price_note": "Live estimates are not an enforceable cap. Applicable account print, postage, extras and tax charges are paid by the account owner."}
    checksum = payload_digest(manifest, document)
    ident = "ms_" + digest((request_id + ":" + mode).encode())[:32]
    with ledger.db() as db:
        db.execute("BEGIN IMMEDIATE")
        eligible(db, request_id)
        old = db.execute("SELECT * FROM lob_jobs WHERE id=?", (ident,)).fetchone()
        if old:
            if old["state"] in LOCKED or old["claimed_at"]:
                raise InputError("This mailing was already claimed. Only inspect or reconcile it; no new ID or resend is permitted.")
            if old["digest"] == checksum:
                return status(ledger, ident)
            if not replace:
                raise InputError("A different unsubmitted preview exists. Use --replace-unsubmitted, then review and approve again.")
            db.execute("UPDATE lob_jobs SET document=?,manifest=?,digest=?,state='prepared',approval=NULL WHERE id=?",
                       (document, canonical(manifest), checksum, ident))
            audit(db, ident, "preview_replaced", previous_digest=old["digest"], digest=checksum)
        else:
            db.execute("INSERT INTO lob_jobs(id,request_id,mode,document,manifest,digest,state,created_at) VALUES(?,?,?,?,?,?,'prepared',?)",
                       (ident, request_id, mode, document, canonical(manifest), checksum, now()))
            audit(db, ident, "prepared", digest=checksum)
    return status(ledger, ident)


def status(ledger, ident):
    with ledger.db() as db:
        row = row_for(db, ident)
        manifest = integrity(row)
        history = [dict(json.loads(r[0]), recorded_at=r[1]) for r in db.execute("SELECT payload,created_at FROM lob_audit WHERE job_id=? ORDER BY sequence", (ident,))]
    state = row["state"]
    label = {"prepared": "Local preview only; nothing submitted.", "approved": "Approved locally; nothing submitted.",
             "submitting": "Outcome uncertain after durable claim; do not retry. Reconcile this mailing.",
             "uncertain": "Provider acceptance is uncertain; do not retry. Reconcile this mailing.",
             "accepted": "Test accepted; no physical mail or charge." if row["mode"] == "test" else "Submitted to Lob. Printing, postal handoff, delivery and record correction are not established."}[state]
    return {"id": ident, "mode": row["mode"], "state": state, "status": label, "digest": row["digest"],
            "manifest": manifest, "receipt": json.loads(row["receipt"]) if row["receipt"] else None,
            "history": history, "mail_reduction_verified": False}


def preview(ledger, ident):
    info = status(ledger, ident)
    with ledger.db() as db:
        document = row_for(db, ident)["document"]
    # Export uses exclusive creation: do not overwrite symlinks or any user file.
    path = ledger.folder / (ident + "-" + info["digest"][:12] + ".pdf")
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 or path.read_bytes() != document:
            raise InputError("The preview path is occupied by a different file. Move it aside; it was not overwritten.")
        os.chmod(path, 0o600)
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
    return {**info, "content_pdf": str(path)}


def check_route_snapshot(db, row, manifest, clock=None):
    request = json.loads(db.execute("SELECT payload FROM requests WHERE id=?", (row["request_id"],)).fetchone()[0])
    if request["kind"] in {"household", "occupant", "former_resident"} and manifest.get("route_snapshot") is None:
        raise InputError("This older advertising preview has no verified postal route. Attach a route, replace the unsubmitted preview and approve it again; do not send the legacy approval.")
    if manifest.get("route_snapshot") is not None:
        current = postal_route(db, row["request_id"], (clock or datetime.now(timezone.utc)).date())
        if current != manifest["route_snapshot"]:
            raise InputError("The contact route changed after this preview. Replace the unsubmitted preview and review it again.")


def approve(ledger, ident, checksum, *, reviewed=False, accept_variable_price=False, clock=None):
    if not reviewed:
        raise InputError("Approval requires confirmation that the exact PDF, addresses, service and price basis were reviewed.")
    with ledger.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = row_for(db, ident)
        manifest = integrity(row)
        eligible(db, row["request_id"])
        check_route_snapshot(db, row, manifest, clock)
        if row["state"] not in {"prepared", "approved"} or row["claimed_at"]:
            raise InputError("A claimed mailing cannot be approved again or resent.")
        if checksum != row["digest"]:
            raise InputError("Approval must use the exact current preview digest.")
        if manifest["mode"] == "live":
            if not accept_variable_price:
                raise InputError("Live approval requires acceptance of account charges; the estimate is not a spending cap.")
            price_basis(manifest["price"], "live", manifest["content_pages"], manifest["service"], clock or datetime.now(timezone.utc))
        approval = {"digest": checksum, "reviewed_pdf_addresses_service": True,
                    "accepted_variable_price": bool(accept_variable_price), "approved_at": now()}
        db.execute("UPDATE lob_jobs SET state='approved',approval=? WHERE id=?", (canonical(approval), ident))
        audit(db, ident, "approved", digest=checksum)
    return status(ledger, ident)


def read_key(path, mode):
    path = Path(path).expanduser()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.getuid())):
                raise InputError("The key must be an owner-only regular file with one link (chmod 600).")
            value = handle.read(513)
    except OSError:
        raise InputError("Unable to open the explicitly selected private key file.") from None
    try:
        key = value.decode("ascii").strip()
    except UnicodeError:
        raise InputError("The selected file does not contain a supported Lob API key.") from None
    if not re.fullmatch(r"(?:test|live)_[A-Za-z0-9]{12,480}", key) or not key.startswith(mode + "_"):
        raise InputError("Key format/mode does not match this mailing. No key was printed or stored.")
    return key


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise InputError("Lob returned a redirect; credentials were not forwarded.")


class Transport:
    """Fixed-host transport, no redirect following, no retry, no raw-error echo."""
    def request(self, method, path, key, *, fields=None, document=None, idempotency=None):
        if not (path == "" or re.fullmatch(r"/ltr_[A-Za-z0-9]+", path) or path.startswith("?metadata%5Bmail_stop_id%5D=")):
            raise InputError("Unsupported provider route.")
        headers = {"Authorization": "Basic " + base64.b64encode((key + ":").encode()).decode(), "Lob-Version": VERSION,
                   "Accept": "application/json", "User-Agent": "mail-stop-kit/0.1"}
        body = None
        if method == "POST":
            boundary = "MailStop" + digest(document)[:40]
            chunks = []
            for name, value in fields.items():
                chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
            chunks += [f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="letter.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode(), document, f"\r\n--{boundary}--\r\n".encode()]
            body = b"".join(chunks)
            headers.update({"Content-Type": "multipart/form-data; boundary=" + boundary, "Idempotency-Key": idempotency})
        elif method != "GET":
            raise InputError("Unsupported provider method.")
        request = urllib.request.Request(API + path, data=body, headers=headers, method=method)
        try:
            # Disable environment proxy discovery so key routing is explicit.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=30) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ValueError
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError
                return result
        except Exception:
            raise InputError("Provider request did not yield a verified receipt. Inspect/reconcile the existing attempt; do not resend.") from None


def fields_for(row, manifest):
    fields = {key: str(value).lower() if type(value) is bool else str(value) for key, value in manifest["options"].items()}
    fields.update({f"{role}[{key}]": value for role in ("from", "to") for key, value in manifest[role].items() if value})
    fields.update({"metadata[mail_stop_id]": row["id"], "metadata[mail_stop_digest]": row["digest"], "description": "Postal correction request"})
    if manifest["service"] != "none":
        fields["extra_service"] = manifest["service"]
    return fields


def receipt_for(value, ident, checksum, manifest):
    if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not re.fullmatch(r"ltr_[A-Za-z0-9]+", value["id"]) or value.get("object") != "letter":
        raise InputError("The provider response is not a verified letter resource.")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("mail_stop_id") != ident or metadata.get("mail_stop_digest") != checksum:
        raise InputError("Provider metadata does not match the approved mailing.")
    for role in ("from", "to"):
        observed = value.get(role)
        if not isinstance(observed, dict):
            raise InputError("Provider receipt is missing the approved addresses.")
        # Lob may normalize casing and abbreviations; never silently accept a different unit or city.
        candidate = {key: observed.get(key) or "" for key in manifest[role]}
        if any(not isinstance(v, str) for v in candidate.values()):
            raise InputError("Provider address has invalid field types; the send lock remains.")
        if address_key(address_text(candidate)) != address_key(address_text(manifest[role])) or normalize(candidate["name"]) != normalize(manifest[role]["name"]) or candidate["address_country"] != "US":
            raise InputError("Provider address differs from the approved address. Reconcile it manually; do not resend.")
    # Signed asset URLs, arbitrary metadata and full response bodies are deliberately excluded.
    keep = ("id", "object", "date_created", "send_date", "expected_delivery_date", "status", "tracking_number")
    receipt = {k: value[k] for k in keep if k in value and isinstance(value[k], (str, type(None))) and (value[k] is None or len(value[k]) < 500)}
    receipt["mode"] = manifest["mode"]
    return receipt


def send(ledger, ident, key_file, *, allow_live=False, transport=None, clock=None):
    # Check basic eligibility before reading even an explicitly named key.
    with ledger.db() as db:
        row = row_for(db, ident)
        if row["state"] != "approved" or row["claimed_at"]:
            raise InputError("Only an approved, never-claimed mailing can be submitted. Inspect/reconcile all other states.")
        if row["mode"] == "live" and not allow_live:
            raise InputError("Live mode additionally requires --allow-live for this paid action.")
        mode = row["mode"]
    key = read_key(key_file, mode)
    key_hash = digest(key.encode())
    with ledger.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = row_for(db, ident)
        manifest = integrity(row)
        eligible(db, row["request_id"])
        check_route_snapshot(db, row, manifest, clock)
        approval = json.loads(row["approval"]) if row["approval"] else {}
        if row["state"] != "approved" or row["claimed_at"] or approval.get("digest") != row["digest"] or not approval.get("reviewed_pdf_addresses_service"):
            raise InputError("This mailing is unapproved or already claimed; another submission is forbidden.")
        if manifest["mode"] == "live":
            if not allow_live or not approval.get("accepted_variable_price"):
                raise InputError("Live price consent is missing.")
            price_basis(manifest["price"], "live", manifest["content_pages"], manifest["service"], clock or datetime.now(timezone.utc))
        db.execute("UPDATE lob_jobs SET state='submitting',claimed_at=?,key_hash=? WHERE id=?", (now(), key_hash, ident))
        audit(db, ident, "submission_claimed", digest=row["digest"])
    # COMMIT has completed. Any exception or process death after this point leaves a durable lock.
    try:
        value = (transport or Transport()).request("POST", "", key, fields=fields_for(row, manifest), document=row["document"], idempotency=ident)
        receipt = receipt_for(value, ident, row["digest"], manifest)
    except Exception:
        with ledger.db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE lob_jobs SET state='uncertain' WHERE id=? AND state='submitting'", (ident,)).rowcount
            if changed:
                audit(db, ident, "submission_uncertain")
            accepted = row_for(db, ident)["state"] == "accepted"
        if accepted:
            return status(ledger, ident)  # A concurrent reconciliation already found stronger evidence.
        raise InputError("Submission outcome is uncertain and locked. Use lob-reconcile; do not retry or create another ledger/ID.") from None
    with ledger.db() as db:
        db.execute("UPDATE lob_jobs SET state='accepted',receipt=? WHERE id=?", (canonical(receipt), ident))
        audit(db, ident, "provider_accepted", provider_id=receipt["id"], mode=mode)
    return status(ledger, ident)


def reconcile(ledger, ident, key_file, *, transport=None):
    with ledger.db() as db:
        row = row_for(db, ident)
        manifest = integrity(row)
        if row["state"] not in LOCKED or not row["claimed_at"]:
            raise InputError("Reconciliation is only for an already claimed attempt.")
    key = read_key(key_file, row["mode"])
    if digest(key.encode()) != row["key_hash"]:
        raise InputError("Reconciliation requires the same explicitly selected key/account as the original attempt. A rotated key requires manual provider support review.")
    client = transport or Transport()
    previous = json.loads(row["receipt"]) if row["receipt"] else None
    if previous:
        values = [client.request("GET", "/" + previous["id"], key)]
    else:
        query = "?" + urllib.parse.urlencode({"metadata[mail_stop_id]": ident, "limit": 2})
        response = client.request("GET", query, key)
        values = response.get("data")
        if not isinstance(values, list) or len(values) != 1 or response.get("next_url"):
            raise InputError("No unique complete provider match was found. This does not prove nothing was created; the local send lock remains.")
    receipt = receipt_for(values[0], ident, row["digest"], manifest)
    if previous and receipt["id"] != previous["id"]:
        raise InputError("The provider resource ID changed; keep the attempt locked for manual review.")
    with ledger.db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE lob_jobs SET state='accepted',receipt=? WHERE id=?", (canonical(receipt), ident))
        audit(db, ident, "provider_reconciled", provider_id=receipt["id"], mode=row["mode"])
    return status(ledger, ident)
