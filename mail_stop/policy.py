"""Conservative rules adapted from Mailroom, independent of any vision model.

Input observations are untrusted. reviewed_fields records what a person checked;
it is a declaration, not proof of identity or a calibrated model confidence.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date

KINDS = {"advertisement", "potentially_important", "personal", "unknown"}
MAIL_CLASSES = {"first_class", "marketing", "periodical", "unknown"}
ENDORSEMENTS = {"Return Service Requested", "Address Service Requested", "Change Service Requested",
                "Electronic Service Requested", "Forwarding Service Requested"}
REVIEW_FIELDS = {"sender", "recipient", "address", "kind", "mail_class", "endorsements", "evidence"}
PROTECTED_CUES = re.compile(
    r"\b(?:open enrollment|benefits enrollment|explanation of benefits|payment due|amount due|"
    r"past due|minimum payment|jury summons|renewal notice|tax notice|court notice|collection notice)\b", re.I)
OCCUPANT = re.compile(r"(?:^|\bor\b)\s*(?:current\s+)?(?:resident|occupant|householder)\s*(?:$|\bor\b)", re.I)
RETURN_NOTE = ('For a confirmed former resident, leave the envelope unopened, mark "Not at this address" '
               'without covering the address, and return it to the carrier or a USPS collection box. '
               'This does not prove a sender changed its records.')


class InputError(ValueError):
    """Invalid or insufficient input; messages do not include private values."""


def normalize(value: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def address_key(value: str) -> str:
    # Preserve unit numbers and ambiguous OCR. Only common spellings are aliases.
    words = normalize(value).split()
    if len(words) >= 2 and re.fullmatch(r"\d{5}", words[-2]) and re.fullmatch(r"\d{4}", words[-1]):
        words.pop()
    aliases = {"street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd", "drive": "dr",
               "lane": "ln", "apartment": "unit", "apt": "unit", "suite": "unit",
               "north": "n", "south": "s", "east": "e", "west": "w"}
    return " ".join(aliases.get(word, word) for word in words)


def text(value, label, maximum=2000, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise InputError(f"{label} must be nonempty text of at most {maximum} characters.")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise InputError(f"{label} contains control characters.")
    return value.strip()


def exact_keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise InputError(f"{label} must be an object with only documented fields.")


def names(value, label):
    if not isinstance(value, list) or len(value) > 40:
        raise InputError(f"{label} must be a list of at most 40 names.")
    result = [text(item, label, 150) for item in value]
    if any(not normalize(item) for item in result):
        raise InputError(f"{label} contains an empty normalized name.")
    return result


def validate_household(value):
    exact_keys(value, {"address", "residents", "former_residents"}, "Household")
    out = {"address": text(value.get("address"), "Household address", 400),
           "residents": names(value.get("residents", []), "Residents"),
           "former_residents": names(value.get("former_residents", []), "Former residents")}
    if not address_key(out["address"]):
        raise InputError("Household address cannot be empty after normalization.")
    if {normalize(n) for n in out["residents"]} & {normalize(n) for n in out["former_residents"]}:
        raise InputError("A name cannot be both a resident and a confirmed former resident.")
    return out


def validate_piece(value):
    allowed = {"id", "sender", "recipient", "address", "kind", "evidence", "current_resident", "is_mailpiece",
               "reviewed_fields", "mail_class", "endorsements", "prescreen_notice", "forwarded", "lookalike",
               "received_on"}
    exact_keys(value, allowed, "Mailpiece")
    out = {key: text(value.get(key, ""), key, 2000 if key == "evidence" else 400, allow_empty=key != "id")
           for key in ("id", "sender", "recipient", "address", "evidence")}
    out["kind"] = value.get("kind", "unknown")
    out["mail_class"] = value.get("mail_class", "unknown")
    if not isinstance(out["kind"], str) or not isinstance(out["mail_class"], str) or out["kind"] not in KINDS or out["mail_class"] not in MAIL_CLASSES:
        raise InputError("Unknown mail kind or mail_class.")
    for key, default in (("is_mailpiece", True), ("current_resident", False), ("prescreen_notice", False),
                         ("forwarded", False), ("lookalike", False)):
        out[key] = value.get(key, default)
        if type(out[key]) is not bool:
            raise InputError(f"{key} must be true or false.")
    for key, accepted in (("reviewed_fields", REVIEW_FIELDS), ("endorsements", ENDORSEMENTS)):
        values = value.get(key, [])
        if not isinstance(values, list) or len(values) > len(accepted) or any(not isinstance(v, str) or v not in accepted for v in values):
            raise InputError(f"{key} contains an unsupported value.")
        out[key] = sorted(set(values))
    out["received_on"] = value.get("received_on")
    if out["received_on"] is not None:
        try:
            if not isinstance(out["received_on"], str) or date.fromisoformat(out["received_on"]).isoformat() != out["received_on"]:
                raise ValueError
        except ValueError:
            raise InputError("received_on must be an ISO date (YYYY-MM-DD).") from None
    return out


def recipient_role(piece, household):
    name = normalize(piece["recipient"])
    # Literal wording is recognized even if an extractor missed its boolean.
    if piece["current_resident"] or OCCUPANT.search(name) or name in {"postal customer", "residential customer"}:
        return "current_resident"
    if name and name in {normalize(n) for n in household["residents"]}:
        return "household"
    if name and name in {normalize(n) for n in household["former_residents"]}:
        return "former_resident"
    return "unknown"


def decide(piece, household):
    role = recipient_role(piece, household)
    result = {"recipient_role": role, "route": "review", "reason": "Review this piece before preparing any request.",
              "return_note": None, "protected": bool(PROTECTED_CUES.search(piece["evidence"]))}
    if not piece["is_mailpiece"]:
        return {**result, "route": "ignore_nonmail", "reason": "Not a mailpiece."}
    reviewed = set(piece["reviewed_fields"])
    if not {"recipient", "address"} <= reviewed or not piece["address"]:
        return {**result, "reason": "A person must check the recipient and full delivery address, including the unit."}
    if address_key(piece["address"]) != address_key(household["address"]):
        return {**result, "reason": "The observed address does not match the confirmed household address; do not guess a unit or old address."}
    if piece["forwarded"]:
        return {**result, "reason": "Forwarded mail needs review of the original and forwarding addresses before making record-correction claims."}
    if role == "unknown":
        return {**result, "reason": "An unfamiliar name is not a confirmed former resident. Ask the household before using that claim."}
    if piece["lookalike"]:
        return {**result, "reason": "Verify this sender independently; the envelope may imitate an official notice."}
    if role == "former_resident":
        result["return_note"] = RETURN_NOTE
        if piece["kind"] == "advertisement" and not result["protected"]:
            if {"sender", "kind"} <= reviewed and piece["sender"].strip():
                if {"mail_class", "endorsements"} <= reviewed and piece["mail_class"] == "marketing" and not piece["endorsements"]:
                    return {**result, "route": "draft_former_resident", "reason": "Unendorsed undeliverable Marketing Mail is disposed of by USPS. Prepare a sender-specific correction as the current occupant."}
                return {**result, "route": "draft_former_resident", "reason": "Prepare a sender-specific advertising correction as the current occupant. Returning a piece is not proof of list correction."}
        return {**result, "route": "return_unopened", "reason": "Confirmed former resident. Return the sealed piece; do not act on their accounts or notices."}
    if result["protected"] or piece["kind"] != "advertisement":
        return {**result, "protected": True, "reason": "Keep this out of advertising removals. Envelope wording does not establish its contents or an amount due."}
    if not {"sender", "kind"} <= reviewed or not piece["sender"].strip():
        return {**result, "reason": "Check the sender and advertising classification before preparing a removal draft."}
    if piece["prescreen_notice"]:
        return {**result, "route": "self_service_prescreen", "reason": "Use the named consumer's own OptOutPrescreen process. A current-occupant role does not authorize changing someone else's credit-file preferences."}
    return {**result, "route": "draft_occupant" if role == "current_resident" else "draft_household",
            "reason": "Prepare an advertising-only request. Verify the sender's official contact route before any submission."}


def request_id(request):
    # Stable per purpose, sender, role/name and address, independent of scan IDs.
    target = [request["kind"], normalize(request["sender"]), normalize(request["recipient"]), address_key(request["address"])]
    return hashlib.sha256(json.dumps(target, ensure_ascii=False).encode()).hexdigest()[:24]


def draft(piece, household, decision):
    route = decision["route"]
    if route not in {"draft_household", "draft_occupant", "draft_former_resident"}:
        return None
    address = household["address"]
    if route == "draft_household":
        body = (f"Please remove {piece['recipient']} at the address below from your promotional postal mailing lists. "
                "This request concerns advertising only, not account statements or required notices.")
        recipient = piece["recipient"]
    elif route == "draft_occupant":
        body = ("I am a current occupant of the address below. Please stop your promotional postal mail addressed to "
                "Current Resident, Occupant, or similar address-based recipients at this address. "
                "This request concerns advertising only, not account statements or required notices.")
        recipient = "Current Resident"
    else:
        body = (f"I am a current occupant of the address below. Advertising addressed to {piece['recipient']} reaches this "
                "household, but the household has confirmed that this person no longer lives here. Please correct your "
                "advertising mailing records for this name at this address. I am contacting you as the current occupant, "
                "not on that person's behalf. This is not a request to close or change their accounts.")
        recipient = piece["recipient"]
    request = {"kind": route.removeprefix("draft_"), "sender": piece["sender"], "recipient": recipient,
               "address": address, "subject": "Promotional postal mail correction",
               "body": body + f"\n\nMailing address:\n{address}\n\nPlease confirm whether you can process this request.",
               "destination": None}
    request["id"] = request_id(request)
    return request


def plan(household_value, pieces_value):
    household = validate_household(household_value)
    if not isinstance(pieces_value, list) or len(pieces_value) > 1000:
        raise InputError("Mail input must be a list of at most 1,000 pieces.")
    pieces = [validate_piece(value) for value in pieces_value]
    if len({p["id"] for p in pieces}) != len(pieces):
        raise InputError("Mailpiece IDs must be unique within an input file.")
    rows = []
    for piece in pieces:
        decision = decide(piece, household)
        rows.append({"piece": piece, "decision": decision, "request": draft(piece, household, decision)})
    return {"schema_version": 1, "household": household, "pieces": rows}


def office_draft(plan_value, piece_ids, office_address):
    """Only confirmed former-resident pieces at the checked current address qualify."""
    office_address = text(office_address, "Verified local office address", 400)
    if not piece_ids or len(set(piece_ids)) != len(piece_ids):
        raise InputError("Select one or more distinct mailpiece IDs for the office draft.")
    by_id = {row["piece"]["id"]: row for row in plan_value["pieces"]}
    selected = []
    for ident in piece_ids:
        row = by_id.get(ident)
        if row is None or row["decision"]["recipient_role"] != "former_resident" or row["decision"]["route"] not in {"return_unopened", "draft_former_resident"}:
            raise InputError("Office drafts require selected, reviewed former-resident pieces at the current address.")
        selected.append(row["piece"])
    former_names = sorted({p["recipient"] for p in selected})
    address = plan_value["household"]["address"]
    observations = "\n".join(f"- {p['received_on'] or 'Date not recorded'}: envelope addressed to {p['recipient']}" for p in selected)
    request = {"kind": "delivery_correction", "sender": "Local USPS delivery office", "recipient": "; ".join(former_names),
               "address": address, "subject": "Repeated delivery for confirmed former residents",
               "destination": {"name": "Local Postmaster / Delivery Supervisor", "address": office_address},
               "body": ("To the local delivery office:\n\nI am a current occupant of the address below. The household has confirmed "
                        "that the following people no longer live here:\n" + "\n".join(f"- {name}" for name in former_names)
                        + f"\n\nHousehold address:\n{address}\n\nSelected envelope observations:\n{observations}\n\n"
                        "Please review these repeated deliveries with the carrier or delivery supervisor and advise whether the relevant delivery records need correction. "
                        "Please advise how undeliverable mail for these confirmed former residents should be handled locally. "
                        "I am contacting you as the current occupant, not as or on behalf of the former residents. "
                        "I am not requesting a change of address or changes to anyone's accounts. "
                        "This request excludes mail addressed to Current Resident or Occupant. Thank you.")}
    request["id"] = request_id(request)
    return request, selected


def household_kit(household_value, recipient, updated_on=None):
    """Two paper notes for the current household; never a mailing to a former resident."""
    household = validate_household(household_value)
    recipient = text(recipient, "Kit recipient", 150)
    if normalize(recipient) not in {normalize(n) for n in household["residents"]}:
        raise InputError("The kit recipient must be a confirmed current household resident.")
    if not household["residents"]:
        raise InputError("Confirm the current household names before creating a mailbox kit.")
    updated_on = updated_on or date.today().isoformat()
    try:
        if date.fromisoformat(updated_on).isoformat() != updated_on:
            raise ValueError
    except (TypeError, ValueError):
        raise InputError("The kit date must be YYYY-MM-DD.") from None
    request = {"kind": "household_kit", "sender": "Current household", "recipient": recipient,
               "address": household["address"], "subject": "Two household mailbox notes",
               "destination": {"name": recipient, "address": household["address"]},
               "kit_residents": household["residents"], "kit_former_residents": household["former_residents"], "kit_updated_on": updated_on,
               "body": "Two printable paper notes: a current-resident name note to keep by the mailbox and a separate note the carrier may take for review. Not an official USPS form or a guarantee of delivery-record correction."}
    # A changed roster does not silently create a second paid attempt for the same address.
    request["id"] = hashlib.sha256(("household_kit:" + address_key(household["address"])).encode()).hexdigest()[:24]
    return request
