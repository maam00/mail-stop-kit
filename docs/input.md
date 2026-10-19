# Input format

Use JSON files outside this repository for real household data. [examples/](../examples/) is entirely fictional. There are no automatic image, mailbox or model integrations. A person or their existing agent transcribes selected evidence, then a person checks the fields used for a draft.

`household.json`:

```json
{
  "address": "100 Example Lane Unit 4\nExampletown, FL 00000",
  "residents": ["Alex Example", "Sam Example"],
  "former_residents": ["Taylor Former"]
}
```

Use only names/aliases the household confirmed. A name cannot be in both lists. This declaration is not identity verification. A Current Resident piece can be handled without adding its departed printed name to either list.

`mail.json` is an array of observations:

```json
[
  {
    "id": "selected-envelope-1",
    "sender": "Example Catalog Company",
    "recipient": "Alex Example",
    "address": "100 Example Lane Unit 4\nExampletown, FL 00000",
    "kind": "advertisement",
    "evidence": "Selected envelope says seasonal catalog",
    "reviewed_fields": ["sender", "recipient", "address", "kind"],
    "received_on": "2026-10-01"
  }
]
```

| Field | Meaning |
|---|---|
| `id` | Your unique local observation label, not a file path or executable command. |
| `sender`, `recipient`, `address` | Observed text. Keep the full unit and postal code. Missing or ambiguous text stays empty and goes to review. |
| `kind` | `advertisement`, `potentially_important`, `personal`, or `unknown`. Defaults to `unknown`. Visible protected cues override an advertising label. |
| `evidence` | Short literal envelope cues, not inferred contents, balances or deadlines. |
| `reviewed_fields` | Fields a person actually checked. Recipient and address review is required for a routing action. Sender and kind review is additionally required for advertising drafts. Defaults to `[]`. |
| `current_resident` | Optional boolean for occupant wording observed on the envelope. Literal “or Current Resident” in recipient text is also recognized. Defaults to false. |
| `is_mailpiece` | Optional boolean; false excludes logos/ride-along art. Defaults to true. |
| `mail_class` | `first_class`, `marketing`, `periodical`, or `unknown`; defaults to unknown. |
| `endorsements` | List of recognized service endorsements, below. Defaults to `[]`, but absence is not considered reviewed unless `endorsements` appears in `reviewed_fields`. |
| `prescreen_notice` | Boolean indicating a prescreened credit/insurance offer; directs household advertising to the consumer's own official process. |
| `forwarded`, `lookalike` | Optional booleans. Either causes review before a correction draft. |
| `received_on` | Optional `YYYY-MM-DD` of the selected observation. An email's date is not proof of physical delivery. |

Recognized endorsements: Return Service Requested, Address Service Requested, Change Service Requested, Electronic Service Requested, Forwarding Service Requested. The kit does **not** infer that every endorsement returns the physical piece; USPS handling varies.

`reviewed_fields` accepts sender, recipient, address, kind, evidence, mail_class and endorsements. A future observation should get a different `id`; repeated imports of identical observations are deduplicated. Correcting a source file does not erase evidence already saved in the ledger. Changed draft wording for the same target is refused rather than silently overwriting history; this release has no draft-revision command. Review the existing case before changing scope.

Inputs are limited to 1,000 pieces and 2 MB per file. Extra fields and unsupported types are rejected. Short strings, strict enums and deterministic rules make invalid inputs visible; they do not prove a model read an envelope correctly. Keep uncertain OCR out of the reviewed fields.
