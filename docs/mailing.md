# Optional Lob letters: your key, your account

The review core works without Lob. Install `.[lob]` for local PDF generation. Only `lob-send` and `lob-reconcile` make provider requests. Test mode is the default; a user supplies their own Lob account/key and pays Lob directly for live letters. There is no hosted middleman or fee collected by this kit.

This release candidate has been tested offline with fictional inputs and fake provider transports. Authenticated Lob test-mode qualification and any real send remain unperformed for this kit. Historical household work included a sent-letter artifact; that history informed the generic template, not a claim that this extracted adapter was live-tested. No original letter, household identifiers or provider receipt is included.

## Prepare and inspect locally

First create a reviewed draft with `prepare`, `office-draft` or `household-kit`. Advertising drafts additionally need an explicitly verified postal route with completed prerequisites (`attach-route`); email/form routes cannot be silently replaced by paid letters. The route snapshot is bound to the preview and checked again at approval and sending. A physical enclosure such as a catalog label is unsupported by this renderer; those routes remain manual. A wet signature, provider form or altered-document requirement also needs a separately reviewed manual route; the fixed renderer does not satisfy it. Household kits use the current household as destination, and office letters retain their separate verified office destination. Store real inputs outside this repository. Create an addresses JSON like `examples/lob-addresses.json`, replacing its fictional values privately. Use the verified organization's postal destination, not the former resident's forwarding address. The `from` name is the authorized current occupant, not an impersonation of a former resident. `destination_verified` records your check; the kit is not an address-verification service.

```sh
mail-stop lob-prepare DRAFT_ID --addresses PRIVATE_ADDRESSES.json --data PRIVATE_FOLDER
mail-stop lob-preview MAILING_ID --data PRIVATE_FOLDER
```

Both commands stay local. Open the returned PDF and inspect the entire manifest. PDF text is escaped and rendered locally, with no remote assets, hidden attachments or generated signature. Supported: US letter paper, black-and-white, single-sided, USPS First-Class, operational use, one to five content pages, domestic states/DC. Unsupported font characters stop generation rather than silently changing a name.

**This is the exact uploaded content PDF, not Lob's final envelope proof.** Lob inserts a billable address page; certified service adds its own barcode/address material. The manifest discloses the additional page. Lob's final rendering is created after submission; this kit does not fetch signed preview URLs. Sending uses the immutable PDF bytes in SQLite, not a possibly modified export.

Default extra service is `none`. Choose `--service certified` or `--service certified_return_receipt` only when wanted and priced. USPS does not prescribe certified mail for local complaints. Certified Mail provides mailing and delivery/attempt information; Return Receipt adds a signature record. Neither establishes correction. [USPS complaints, DMM 608 §6.1](https://pe.usps.com/text/dmm300/608.htm), [USPS extra services](https://www.usps.com/ship/insurance-extra-services.htm).

## Put your key in an explicit private file

Create your own account and key through Lob. Keep its value out of agent context, commands, logs and Git. A local terminal can prompt without echoing it:

```sh
python3 - "$HOME/.config/mail-stop/lob-test.key" <<'PY'
import getpass, os, pathlib, sys
path = pathlib.Path(sys.argv[1]).expanduser()
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
key = getpass.getpass("Paste your Lob test key (hidden): ")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write(key + "\n")
PY
```

This refuses to overwrite an existing file. The adapter reads only the path you supply, enforces owner-only POSIX permissions and matching `test_`/`live_` mode, and never persists the key. Keep a distinct file for a live key. Other operating systems require their own file access controls.

## Approve one exact preview, then test

Use the full digest returned by `lob-preview`. The person must review the PDF, destination and return addresses, service and pricing before these flags are set. An agent must obtain specific approval from the human; flags cannot prove consent happened.

```sh
mail-stop lob-approve MAILING_ID --digest EXACT_DIGEST --reviewed-pdf-addresses-service --data PRIVATE_FOLDER
mail-stop lob-send MAILING_ID --key-file PRIVATE_TEST_KEY_FILE --data PRIVATE_FOLDER
```

The second command makes a real test-mode API request. It transmits letter/address data to Lob but creates no physical mail or charges. Use fictional content for qualification. This repository's tests do **not** run that command against Lob. Success is labeled **Test accepted**, never mailed or delivered.

## Live letters require a current applicable price basis

Prepare a separate live preview with `--mode live --price PRIVATE_PRICE.json`. A test approval cannot authorize live mode. Check your own Lob account plan and applicable print/postage/extras/tax pricing first. Public starting prices are not an account quote.

Example shape below contains **fictional amounts, not current pricing**. Replace every value from the applicable account and the actual PDF/service:

```json
{
  "estimated_total_usd": "99.00",
  "checked_at": "2026-10-19T12:00:00+00:00",
  "source_url": "https://dashboard.lob.com/",
  "account_plan": "Replace with the applicable account plan",
  "content_pages": 1,
  "billable_pages": 2,
  "service": "none",
  "assumptions": "Replace with applicable print, added address page, postage, selected extras, taxes and any uncertainty."
}
```

The check must be within 24 hours at preparation, approval **and sending**. Page counts and service must match; billable pages include the added address page. The operator attests pricing applies to the account behind their key. The kit does not authenticate a billing quote or retrieve account prices.

**The estimate is not an enforceable cap.** Lob's create-letter API exposes no maximum-charge field. The account owner accepts applicable actual charges, which may differ. If the person requires a hard cap, do not use this live path.

```sh
mail-stop lob-prepare DRAFT_ID --addresses PRIVATE_ADDRESSES.json --mode live --service none --price PRIVATE_PRICE.json --data PRIVATE_FOLDER
mail-stop lob-approve LIVE_MAILING_ID --digest EXACT_LIVE_DIGEST --reviewed-pdf-addresses-service --accept-variable-price --data PRIVATE_FOLDER
mail-stop lob-send LIVE_MAILING_ID --key-file PRIVATE_LIVE_KEY_FILE --allow-live --data PRIVATE_FOLDER
```

Live send submits a paid printing request and can incur charges. A matching receipt is **Submitted to Lob**. Rendering, printing, postal handoff, delivery and correction are separate facts. A resource with rendering failure is still an existing resource; do not blindly send another.

Before any claim, a stale estimate or changed address/service can be replaced with `lob-prepare ... --replace-unsubmitted`. This retains the mailing ID, clears approval and requires fresh review. After a claim, replacement and reapproval are blocked permanently.

## Reconcile uncertainty; never retry a POST

```sh
mail-stop lob-status MAILING_ID --data PRIVATE_FOLDER
mail-stop lob-reconcile MAILING_ID --key-file SAME_PRIVATE_KEY_FILE --data PRIVATE_FOLDER
```

Status is local. Reconcile reads the known resource, or lists only resources with this exact metadata ID. It requires one matching resource, digest and addresses. No match, multiple matches, changed addresses or account/key mismatch leave the send lock intact. No match does **not** prove nothing was accepted. Rotated keys require manual account/provider review rather than bypassing the check.

The kit commits `submitting` and a stable idempotency key **before HTTP**. A timeout, malformed response, HTTP rejection or process death leaves a permanent local lock. Even a clearly rejected request conservatively needs manual review; this version offers no automatic unlock or retry. Lob's idempotency protection lasts **24 hours**, after which the same key can create another resource. This kit never repeats the POST, including within that window.

Keep one canonical ledger and private backups. Do not delete it, copy it to another active machine, change rows or create a new request ID to resend. Local protection cannot deduplicate unrelated tools or restored/stale copies. This version does not implement cancellation, refunds, tracking webhooks, batch sending or automatic follow-up letters.

## Official contract checked October 19, 2026

- [Lob API reference](https://docs.lob.com/): multipart PDFs, explicit size/API version, service options, metadata, address placement and returned resources.
- [Lob letter schema](https://github.com/lob/lob-openapi/blob/main/resources/letters/letters.yml), [address placement](https://github.com/lob/lob-openapi/blob/main/resources/letters/attributes/address_placement.yml), [metadata filter](https://github.com/lob/lob-openapi/blob/main/shared/parameters/metadata.yml): the adapter pins `Lob-Version: 2024-01-01` for explicit `size=us_letter`, uses `address_placement=insert_blank_page` (billable additional page), and the list query uses the documented deep-object `metadata[mail_stop_id]` filter. No create-letter price-cap field is present. These were documentation checks, not authenticated provider validation.
- [Lob mail settings and idempotency](https://help.lob.com/print-and-mail/building-a-mail-strategy/managing-mail-settings): 24-hour idempotency lifetime.
- [Lob pricing](https://www.lob.com/pricing), [billing](https://help.lob.com/account-management/billing): plan and service-dependent charges; confirm the account's rates.
- [Lob test-key guidance](https://help.lob.com/print-and-mail/integrations/api-integrations/zapier): test mode creates no physical mail or charges.
- [Lob reconciliation guide](https://help.lob.com/developer-docs/use-case-guides/ingesting-tracking-events-with-webhooks): resource retrieval/list metadata filters and separate tracking facts.
