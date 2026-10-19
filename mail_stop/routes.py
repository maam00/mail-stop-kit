"""Local contact-route evidence and next steps. No research, navigation or sends."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from importlib.resources import files
from urllib.parse import urlsplit

from .ledger import canonical, now
from .policy import InputError, exact_keys, normalize, text

CHANNELS = {"email", "form", "postal", "phone"}
PURPOSES = {"household": "promotional_postal_mail", "occupant": "promotional_postal_mail",
            "former_resident": "promotional_postal_mail", "delivery_correction": "local_delivery_correction"}
FIELDS = {"directory_id", "sender_exact", "provider_scope", "purpose", "channel", "destination", "source_url", "checked_on",
          "verification", "scope_confirmed", "authority_confirmed", "eligibility", "required_steps", "steps_confirmed",
          "confirmation_required", "processing_window", "review_on", "requires_physical_enclosure"}
MAX_ROUTE_AGE_DAYS = 30  # Local review policy, not a provider promise.


def day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise InputError("Route dates must use YYYY-MM-DD.") from None


def public_url(value):
    value = text(value, "Official source/destination URL", 1000)
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or any(c.isspace() for c in value):
            raise ValueError
    except ValueError:
        raise InputError("Use a public HTTPS URL without embedded credentials, fragments or whitespace.") from None
    return value


def directory():
    return json.loads(files("mail_stop").joinpath("route_directory.json").read_text(encoding="utf-8"))


def template(ident):
    for item in directory()["routes"]:
        if item["directory_id"] == ident:
            return item
    raise InputError("No exact route-directory ID matches. No fuzzy provider matching is performed.")


def validate(value, request, today=None):
    today = today or date.today()
    exact_keys(value, FIELDS, "Route")
    required = FIELDS - {"directory_id", "processing_window", "review_on"}
    if not required <= set(value):
        raise InputError("The route is missing documented scope, verification or required-step fields.")
    if request["kind"] not in PURPOSES or value.get("purpose") != PURPOSES[request["kind"]]:
        raise InputError("The route purpose does not match this request. Household kits do not use sender-removal routes.")
    out = {key: text(value.get(key), key, 1500 if key == "eligibility" else 600)
           for key in ("sender_exact", "provider_scope", "purpose", "channel", "destination", "verification", "eligibility")}
    if normalize(out["sender_exact"]) != normalize(request["sender"]):
        raise InputError("Explicitly match the route to this exact sender; a similar brand name is insufficient.")
    if out["channel"] not in CHANNELS or out["verification"] not in {"researched", "verified"}:
        raise InputError("Unsupported route channel or verification state.")
    out["source_url"] = public_url(value["source_url"])
    out["checked_on"] = day(value["checked_on"]).isoformat()
    if day(out["checked_on"]) > today:
        raise InputError("A route's checked date cannot be in the future.")
    for flag in ("scope_confirmed", "authority_confirmed", "steps_confirmed", "confirmation_required", "requires_physical_enclosure"):
        if type(value.get(flag)) is not bool:
            raise InputError("Route confirmation/eligibility flags must be true or false.")
        out[flag] = value[flag]
    if out["verification"] == "verified" and not (out["scope_confirmed"] and out["authority_confirmed"]):
        raise InputError("A verified route requires explicit sender-scope and authority confirmation.")
    steps = value["required_steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise InputError("List one to twenty specific steps required by this route.")
    out["required_steps"] = [text(v, "Required step", 1000) for v in steps]
    if out["channel"] == "email" and not re.fullmatch(r"[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+", out["destination"]):
        raise InputError("Use one exact email address, not a guessed address or a list.")
    if out["channel"] == "form":
        out["destination"] = public_url(out["destination"])
    if out["channel"] == "phone" and not re.fullmatch(r"\+?[0-9(). -]{7,40}", out["destination"]):
        raise InputError("Use the exact public phone number from the official source.")
    out["processing_window"] = text(value.get("processing_window", ""), "Provider-stated processing window", 600, allow_empty=True)
    out["review_on"] = day(value["review_on"]).isoformat() if value.get("review_on") else None
    out["directory_id"] = value.get("directory_id")
    if out["directory_id"] is not None:
        known = template(text(out["directory_id"], "Directory ID", 100))
        # Directory suggestions cannot silently be repurposed across business/consumer scopes.
        for key in ("sender_exact", "provider_scope", "purpose", "channel", "destination", "source_url", "eligibility",
                    "required_steps", "confirmation_required", "processing_window", "requires_physical_enclosure"):
            if out[key] != known[key]:
                raise InputError("This attachment changes the directory route's source, destination or scope. Use a separately researched custom route instead of mislabeling it.")
    return out


def attach(ledger, ident, value, today=None):
    entry = ledger.get(ident)
    route = validate(value, entry["request"], today)
    encoded = canonical(route)
    checksum = hashlib.sha256((ident + encoded).encode()).hexdigest()
    with ledger.db() as db:
        db.execute("BEGIN IMMEDIATE")
        if any(e["kind"] == "closed" for e in ledger._events(db, ident)):
            raise InputError("This case is closed; it will not be reopened by attaching a route.")
        prior = db.execute("SELECT payload FROM routes WHERE request_id=? ORDER BY sequence DESC LIMIT 1", (ident,)).fetchone()
        if prior and prior[0] == encoded:
            return False
        # Keep every revision, including a deliberate return to earlier source data.
        checksum = hashlib.sha256((checksum + now()).encode()).hexdigest()
        db.execute("INSERT INTO routes(hash,request_id,payload,created_at) VALUES(?,?,?,?)", (checksum, ident, encoded, now()))
    return True


def latest(db, ident):
    row = db.execute("SELECT payload FROM routes WHERE request_id=? ORDER BY sequence DESC LIMIT 1", (ident,)).fetchone()
    return json.loads(row[0]) if row else None


def ready(route, today=None):
    today = today or date.today()
    return (route and route["verification"] == "verified" and route["scope_confirmed"] and route["authority_confirmed"]
            and timedelta(0) <= today - day(route["checked_on"]) <= timedelta(days=MAX_ROUTE_AGE_DAYS))


def postal_route(db, ident, today=None):
    route = latest(db, ident)
    if not ready(route, today) or route["channel"] != "postal" or not route["steps_confirmed"]:
        raise InputError("This advertising letter needs a currently verified postal route with required steps confirmed; email/form routes do not authorize a postal fallback.")
    if route["requires_physical_enclosure"]:
        raise InputError("This route requires a physical enclosure that the Lob renderer cannot include. Use the reviewed manual contact route.")
    return route


def next_step(entry, today=None):
    today = today or date.today()
    state = entry["summary"]["state"]
    route = dict(entry["routes"][-1]) if entry["routes"] else None
    if route:
        route.pop("recorded_at", None)
    request = entry["request"]
    base = {"id": request["id"], "sender": request["sender"], "kind": request["kind"], "case_state": state,
            "route": route, "automatic_action": False}
    def result(action, instruction):
        return {**base, "next": action, "instruction": instruction}
    if state == "closed_by_user":
        return result("closed", "Closed by the user. Later observations do not reopen or resend this request.")
    if state in {"submission_uncertain", "test_submission_uncertain"}:
        return result("reconcile", "Inspect the existing attempt. For Lob use lob-reconcile; do not submit another request.")
    if state in {"submission_recorded", "provider_acknowledged", "provider_reported_change"}:
        if request["kind"] == "household_kit":
            return result("waiting_for_household_kit", "Provider acceptance is not delivery. After the paper notes arrive, place the name note and give the carrier copy by an agreed handoff; close when you are done.")
        if route and route["confirmation_required"] and state != "provider_reported_change" and not any(e["kind"] == "confirmation_completed" for e in entry["events"]):
            return result("confirmation_needed", "Check the actual provider receipt for its verification step. Approval to submit does not prove that confirmation was completed.")
        if route and route["review_on"] and day(route["review_on"]) <= today:
            return result("review_due", "Your chosen review date has arrived. Inspect actual receipts and later observations; this is not a provider failure deadline or resend instruction.")
        observation = entry["summary"]["latest_mail_observation"]
        changes = [e for e in entry["events"] if e["kind"] == "change_reported"]
        if observation and changes and observation["observed_on"] > changes[-1]["recorded_at"][:10]:
            return result("review_new_observation", "A later piece was observed after a provider-reported change. Review its recipient/address and evidence; do not automatically resend.")
        return result("waiting", "Keep the existing receipt and review any provider instructions or later mail. Acceptance does not establish correction.")
    if request["kind"] in {"household_kit", "delivery_correction"}:
        return result("ready_for_review", "Review the complete local draft and its confirmed destination. A test result is not a real send. No automatic contact.")
    if not ready(route, today):
        return result("verify_route", "Research and explicitly verify the exact sender's current contact route, scope and authority. Bundled routes are suggestions, not household verification.")
    if not route["steps_confirmed"]:
        return result("required_user_steps", "Complete or review the route's listed prerequisites before approving contact. Do not invent missing catalog labels, identity or authority.")
    return result("ready_for_review", "Review this message, exact channel/destination and required steps with the user. Email/form/phone contact uses their existing authorized tools; nothing is sent here.")
