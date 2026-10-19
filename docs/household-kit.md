# Two paper notes for the current household

This extracts the predecessor's existing `mail_kit` path: an ordinary letter is addressed to the current household so the household can use the printed material. It is separate from a complaint letter addressed to a local delivery office.

The PDF has two cutouts on one US Letter sheet:

1. **Mailbox name guide:** the confirmed address/unit, current mail recipients, date, and a reminder that correctly addressed Current Resident/Occupant mail remains valid. No former-recipient names appear on this persistent note.
2. **Carrier copy:** the address, date, current recipients and confirmed former names, with a factual request to review deliveries addressed only to those former recipients. It identifies itself as a household note, not a USPS form or change-of-address request.

No names or aliases are inferred. An unknown recipient must be clarified first. Only names explicitly supplied in the confirmed household input appear. No note says that only listed people may receive mail. If a roster does not fit legibly, generation stops; it does not shrink the text or clip names.

## Prepare locally

Install the optional PDF extra (`pip install ".[lob]"`) even if you plan to print locally and never use Lob.

```sh
mail-stop household-kit --household PRIVATE_HOUSEHOLD.json --recipient "CONFIRMED CURRENT RESIDENT" --data PRIVATE_FOLDER
```

`--updated-on YYYY-MM-DD` sets the printed date; otherwise it uses today. The selected recipient must be a confirmed current resident. The draft ID is stable per household address, so changing the roster/date does not silently generate another paid attempt. A conflicting revised kit requires review; the existing immutable draft is not overwritten.

Use the returned kit ID with `lob-prepare` and a private addresses JSON. Both the return and delivery address must match the confirmed household, and the addressee must be the selected current resident. This fictional shape shows the distinction from a postmaster letter:

```json
{
  "from": {"name": "Alex Example", "address_line1": "100 Example Lane", "address_line2": "Unit 4", "address_city": "Exampletown", "address_state": "FL", "address_zip": "00000"},
  "to": {"name": "Alex Example", "address_line1": "100 Example Lane", "address_line2": "Unit 4", "address_city": "Exampletown", "address_state": "FL", "address_zip": "00000"},
  "destination_verified": true
}
```

```sh
mail-stop lob-prepare KIT_ID --addresses PRIVATE_HOUSEHOLD_ADDRESSES.json --data PRIVATE_FOLDER
```

Open the private PDF. Print it locally, or follow the explicit approval/test/live sequence in [mailing.md](mailing.md). One approval covers one PDF in one ordinary letter envelope. Lob adds its billable address sheet; the two notes are ordinary letter paper, not cardstock, postcards or separately posted items. Certified service is disabled for this kit. No signatures are generated.

Keep real PDFs and inputs outside this repository. Provider acceptance does not prove the kit arrived; a test receipt never counts as a household delivery.

## Using the notes

Cut on the guides. Place the name note inside the box or its name slot without covering the address, lock or opening; adapt placement to the actual mailbox and local guidance. Give the carrier copy directly to the carrier or ask their preferred handoff. Do not assume an unposted loose note placed in a collection box will reach the intended person.

USPS Notice 11, page 5, recommends displaying all mail recipients' names for centralized mailboxes and describes using the mailbox name slot or building-directory maintainer. This is support for a name guide, not an official status for these templates or a requirement that carriers accept a separate handoff note. [USPS Notice 11](https://about.usps.com/notices/not11.pdf), checked October 19, 2026.

A name note does not replace sender-specific removal, guarantee a delivery-record update, or authorize someone else's change of address. The carrier handoff and local-office complaint remain distinct actions.
