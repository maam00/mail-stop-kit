# Changelog

## 0.1.0rc3 — first public alpha

Extracted a small local utility from existing physical-mail cleanup work:

- Distinguish household advertising, confirmed former residents, unfamiliar names, Current Resident mail and protected notices.
- Prepare sender-specific requests, a factual local-delivery-office letter and two printable mailbox notes.
- Keep a private local ledger of requests, contact routes, responses and later mail observations.
- Use six source-linked playbooks and five researched contact routes, with case-specific verification before acting.
- Optionally prepare and submit approved PDFs using the user's own Lob account and key. Test mode is the default; uncertain attempts stay locked pending reconciliation.

The public package contains fictional examples and offline tests. It does not include the predecessor's hosted service, household archive, inbox integration or credentials.

**Lob qualification:** the adapter is tested using fake transports. Authenticated test/live API acceptance, provider rendering, printing and delivery have not been verified for this extracted kit. A request or provider receipt does not prove that mail stopped.
