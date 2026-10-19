# From an unwanted catalog to a draft

This example uses the fictional household and mail already included in the kit. The letter below is the unedited output of `export`, generated from `former-catalog` on October 19, 2026. The image presents that output for readability; it is not an envelope scan, terminal screenshot or provider receipt.

![Fictional catalog addressed to a confirmed former resident, its generated correction draft, and a ledger still marked draft](media/before-after.svg)

## Before: the selected observation

| Field | Reviewed example value |
| --- | --- |
| Sender | Example Home Catalog |
| Addressee | Taylor Former |
| Address | 100 Example Lane Unit 4, Exampletown, FL 00000 |
| Mail | Promotional catalog, addressed only to Taylor |
| Household confirmation | Taylor is a confirmed former resident |

The names, address, postal code and sender are invented. A person or agent transcribes selected evidence into the input format; this kit does not perform OCR or independently establish who lives at an address. The example's review flags describe the fictional setup, not an automated identity check.

## After: the actual exported draft

```text
DRAFT — review the recipient, wording, and official contact route before sending.

Intended organization: Example Home Catalog
Contact route: not verified by this kit.

Subject: Promotional postal mail correction

I am a current occupant of the address below. Advertising addressed to Taylor Former reaches this household, but the household has confirmed that this person no longer lives here. Please correct your advertising mailing records for this name at this address. I am contacting you as the current occupant, not on that person's behalf. This is not a request to close or change their accounts.

Mailing address:
100 Example Lane Unit 4
Exampletown, FL 00000

Please confirm whether you can process this request.
```

The letter speaks as the current occupant, without claiming to be Taylor or asking to close Taylor's accounts. An unfamiliar name would stay on hold for household review. “Taylor Former **or Current Resident**” would get a different, address-based advertising request.

## What the ledger actually records

The relevant fields from `show` after preparing this example are:

```json
{
  "state": "draft",
  "latest_mail_observation": null,
  "mail_reduction_verified": false
}
```

No sender was contacted. There is no provider receipt, delivery result or evidence of reduced mail. Later submissions, replies and observed envelopes are recorded separately in the [outcome history](outcomes.md).

## Reproduce it without an account or key

From the repository folder, on macOS/Linux:

```sh
MAIL_STOP_DEMO=$(mktemp -d)
python3 -m mail_stop prepare --household examples/household.json --mail examples/mail.json --data "$MAIL_STOP_DEMO/ledger"
```

This prepares the six bundled cases, including the catalog above. It creates a temporary local ledger and makes no provider request. The returned IDs identify the eligible drafts. For the unchanged bundled former-catalog example, the ID is `f1d963a10b8a0f07ac8f3b67`:

```sh
python3 -m mail_stop export f1d963a10b8a0f07ac8f3b67 --data "$MAIL_STOP_DEMO/ledger"
python3 -m mail_stop show f1d963a10b8a0f07ac8f3b67 --data "$MAIL_STOP_DEMO/ledger"
```

On Windows, use a new temporary folder path in place of `$MAIL_STOP_DEMO/ledger`. Keep real household inputs and ledgers outside the repository.

## If the right route is a physical letter

The optional Lob adapter connects a reviewed draft to physical-mail submission using **your own account and key**. You pay Lob directly. The path is:

1. Verify a postal contact route for that specific sender. A form or email route is not permission to substitute a paid letter.
2. Prepare the exact content PDF and review the destination and service.
3. Approve that letter and the current account-specific cost before a live send.
4. Submit once and keep the provider reference in the local ledger. An uncertain attempt stays locked for reconciliation, not a fresh send.

The [mailing guide](mailing.md) has the commands. You can try its local test-mode PDF preview without a key, or print the PDF yourself. This page does not include a made-up successful Lob receipt: the extracted adapter has offline test coverage, but its authenticated test/live API, printing and delivery have not been verified.
