"""Local review CLI with explicit, optional Lob submission commands."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from importlib.resources import files
from pathlib import Path

from . import __version__
from .ledger import EVENTS, SOURCES, Ledger, validate_event
from .policy import InputError, household_kit, office_draft, plan


def load(path):
    source = Path(path)
    if source.stat().st_size > 2_000_000:
        raise InputError("Input files must be at most 2 MB; select a smaller batch.")
    with source.open(encoding="utf-8") as handle:
        return json.load(handle)


def output(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def table(plan_value):
    # Render private input as data, not terminal control sequences or commands.
    for row in plan_value["pieces"]:
        p, d = row["piece"], row["decision"]
        print(f"{p['id']} | {d['recipient_role']} | {d['route']}")
        print(f"  {d['reason']}")
        if row["request"]:
            print(f"  Draft ID: {row['request']['id']}")
        if d["return_note"]:
            print(f"  {d['return_note']}")
    print("\nLocal review only. Nothing contacted, submitted, or paid.")


def parser():
    root = argparse.ArgumentParser(description="Review unwanted physical mail, prepare drafts, and keep a private local record. Only lob-send and lob-reconcile contact the provider.")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for name, help_text in (("plan", "Review a selected batch without writing anything."),
                            ("prepare", "Save eligible drafts and observations to a private ledger."),
                            ("office-draft", "Prepare a letter for a verified local delivery office.")):
        p = commands.add_parser(name, help=help_text)
        p.add_argument("--household", required=True, help="JSON file with user-confirmed household names and address.")
        p.add_argument("--mail", required=True, help="JSON array of selected observations; see docs/input.md.")
        if name == "plan":
            p.add_argument("--json", action="store_true", help="Print the full plan, including private draft text, as JSON.")
        else:
            p.add_argument("--data", required=True, help="Dedicated private ledger directory, preferably outside the checkout.")
        if name == "office-draft":
            p.add_argument("--piece-id", action="append", required=True)
            p.add_argument("--office-address", required=True, help="Local delivery office address you independently verified.")
    for name, help_text in (("list", "List drafts and their recorded outcomes."), ("show", "Show a draft and its event history."),
                            ("export", "Print a plain-text draft for review, email or printing."),
                            ("record", "Record evidence of an action performed outside this kit.")):
        p = commands.add_parser(name, help=help_text)
        p.add_argument("--data", required=True)
        if name != "list":
            p.add_argument("id", help="Draft ID returned by plan or prepare.")
        if name == "record":
            p.add_argument("--event", required=True, choices=sorted(EVENTS))
            p.add_argument("--source", required=True, choices=sorted(SOURCES))
            p.add_argument("--evidence", required=True, help="Short factual note; do not paste credentials.")
            p.add_argument("--reference", help="Optional non-secret provider/message reference. Never fetched.")
            p.add_argument("--observed-on", help="Date of the selected envelope/digest observation, YYYY-MM-DD.")
    kit = commands.add_parser("household-kit", help="Prepare two paper mailbox notes to print or mail to a current resident.")
    kit.add_argument("--household", required=True)
    kit.add_argument("--recipient", required=True, help="A confirmed current resident who will receive the paper kit.")
    kit.add_argument("--data", required=True)
    kit.add_argument("--updated-on", help="Date printed on the notes, YYYY-MM-DD; defaults to today.")
    routes = commands.add_parser("routes", help="Show researched public route suggestions, never automatically applied.")
    routes.add_argument("--id", help="Return one exact directory entry as an editable attachment template.")
    route = commands.add_parser("attach-route", help="Record a researched or verified route for one exact sender/case.")
    route.add_argument("id")
    route.add_argument("--route", required=True, help="Local JSON route file; see docs/routes.md.")
    route.add_argument("--data", required=True)
    agenda = commands.add_parser("next", aliases=["agenda"], help="Show local next steps; no jobs, external contact or automatic retries.")
    agenda.add_argument("--data", required=True)
    agenda.add_argument("--id", help="Optional exact case ID; omit to show every case.")
    agenda.add_argument("--on", help="Review as of YYYY-MM-DD; defaults to today.")
    commands.add_parser("playbooks", help="Print dated official-source procedures; this does not verify a live form.")
    for name, help_text in (("lob-prepare", "Prepare a local PDF and address/service/price manifest; default test mode."),
                            ("lob-preview", "Export the exact stored content PDF for review."),
                            ("lob-approve", "Record approval for one immutable preview digest."),
                            ("lob-send", "Submit one approved letter to Lob; live mode may incur account charges."),
                            ("lob-status", "Inspect local mailing evidence without a provider request."),
                            ("lob-reconcile", "Read Lob evidence for an already claimed attempt; never resend.")):
        p = commands.add_parser(name, help=help_text)
        p.add_argument("id", help="Draft ID for lob-prepare; mailing ID for other lob commands.")
        p.add_argument("--data", required=True)
        if name == "lob-prepare":
            p.add_argument("--addresses", required=True)
            p.add_argument("--mode", choices=("test", "live"), default="test")
            p.add_argument("--service", choices=("none", "certified", "certified_return_receipt"), default="none")
            p.add_argument("--price", help="Live account-specific price basis JSON; not an enforced cap.")
            p.add_argument("--replace-unsubmitted", action="store_true")
        elif name == "lob-approve":
            p.add_argument("--digest", required=True)
            p.add_argument("--reviewed-pdf-addresses-service", action="store_true")
            p.add_argument("--accept-variable-price", action="store_true")
        elif name in {"lob-send", "lob-reconcile"}:
            p.add_argument("--key-file", required=True, help="Explicit owner-only key file, never an inline key.")
            if name == "lob-send":
                p.add_argument("--allow-live", action="store_true")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in {"plan", "prepare", "office-draft"}:
            result = plan(load(args.household), load(args.mail))
            if args.command == "plan":
                output(result) if args.json else table(result)
            elif args.command == "prepare":
                output({"draft_ids": Ledger(args.data).add_plan(result), "status": "draft", "submitted": False})
            else:
                request, observations = office_draft(result, args.piece_id, args.office_address)
                ident = Ledger(args.data).add(request, observations)
                output({"draft_id": ident, "status": "draft", "submitted": False})
        elif args.command == "household-kit":
            request = household_kit(load(args.household), args.recipient, args.updated_on)
            ident = Ledger(args.data).add(request, [])
            output({"draft_id": ident, "kind": "household_kit", "status": "draft", "submitted": False})
        elif args.command == "routes":
            from .routes import directory, template
            output(template(args.id) if args.id else directory())
        elif args.command == "playbooks":
            output(json.loads(files("mail_stop").joinpath("playbooks.json").read_text(encoding="utf-8")))
        else:
            ledger = Ledger(args.data)
            if args.command == "attach-route":
                from .routes import attach, next_step
                changed = attach(ledger, args.id, load(args.route))
                output({"route_added": changed, "next_step": next_step(ledger.get(args.id))})
            elif args.command in {"next", "agenda"}:
                from .routes import day, next_step
                entries = [ledger.get(args.id)] if args.id else ledger.list()
                output([next_step(e, day(args.on) if args.on else None) for e in entries])
            elif args.command.startswith("lob-"):
                from . import lob
                if args.command == "lob-prepare":
                    result = lob.prepare(ledger, args.id, load(args.addresses), mode=args.mode, service=args.service,
                                         price=load(args.price) if args.price else None, replace=args.replace_unsubmitted)
                    result = lob.preview(ledger, result["id"])
                elif args.command == "lob-preview":
                    result = lob.preview(ledger, args.id)
                elif args.command == "lob-approve":
                    result = lob.approve(ledger, args.id, args.digest, reviewed=args.reviewed_pdf_addresses_service,
                                         accept_variable_price=args.accept_variable_price)
                elif args.command == "lob-send":
                    result = lob.send(ledger, args.id, args.key_file, allow_live=args.allow_live)
                elif args.command == "lob-reconcile":
                    result = lob.reconcile(ledger, args.id, args.key_file)
                else:
                    result = lob.status(ledger, args.id)
                output(result)
            elif args.command == "list":
                output([{"id": entry["request"]["id"], "kind": entry["request"]["kind"],
                         "sender": entry["request"]["sender"], "summary": entry["summary"], "lob_mailings": entry["lob_mailings"]} for entry in ledger.list()])
            elif args.command == "show":
                output(ledger.get(args.id))
            elif args.command == "export":
                request = ledger.get(args.id)["request"]
                print("DRAFT — review the recipient, wording, and official contact route before sending.\n")
                if request["destination"]:
                    print(f"To: {request['destination']['name']}\n{request['destination']['address']}\n")
                else:
                    print(f"Intended organization: {request['sender']}\nContact route: not verified by this kit.\n")
                print(f"Subject: {request['subject']}\n\n{request['body']}")
            elif args.command == "record":
                event = validate_event(args.event, args.source, args.evidence, args.observed_on, args.reference)
                added = ledger.record(args.id, event)
                output({"event_added": added, "summary": ledger.get(args.id)["summary"]})
        return 0
    except (InputError, json.JSONDecodeError, OSError, sqlite3.Error) as exc:
        # Do not echo bad JSON, source observations, or SQL with household data.
        message = str(exc) if isinstance(exc, InputError) else "Unable to read input or private ledger. Check paths, JSON syntax, and permissions."
        print(f"mail-stop: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
