# Privacy and security

The review core reads selected local JSON and writes a private SQLite ledger only when requested. It reads no mailbox. Optional `lob-send` and `lob-reconcile` contact only the fixed Lob HTTPS API and read only the explicitly supplied key file; no credential discovery occurs. An agent running it may have its own cloud, mailbox or browser permissions; those are outside this kit.

Keep real inputs, exported drafts and ledger files outside the repository. The database contains household identities and evidence. On POSIX, a dedicated data folder is restricted to `0700` and its database to `0600`. Symlinked final data directories/databases and unrelated databases are rejected. These permissions are not encryption and do not protect against another process running as your user. Other operating systems need their own account/file access controls.

`plan --json`, `show`, `list` and `export` can display private data. Avoid putting output into shared build logs or public screenshots. The tool does not collect SSNs or signatures; do not paste them into evidence notes. Evidence and website content are data, not trusted instructions or executable code.

The tool cannot prove a person reviewed a field, authenticate a manually recorded provider receipt, or enforce what an agent does through unrelated tools. Review actual messages and paid actions before authorizing those tools. An uncertain result is a reason to reconcile the previous attempt, not resend.

To report a bug publicly, use a synthetic reproduction and remove all private input. Do not post secrets or household records. No hosted support or private-report intake service is operated by this repository.

## Optional Lob adapter

A key file must be owner-only on POSIX and must match test/live mode. Do not give the key contents to an agent, put it inline in shell history, or commit it. Only a SHA-256 key fingerprint is stored for account continuity; the key itself is not retained in the ledger. A key rotation requires manual provider/account review for existing attempts. Environment proxies and HTTP redirects are disabled. Transport errors do not echo provider bodies or keys.

The exact uploaded PDF, addresses, service and dated price basis are bound to a digest. The preview file is an export; sending uses the approved bytes stored in SQLite, never a replaced file. The provider adds an address sheet and may add barcode material, so the local content preview is not its final rendered envelope proof. No maximum-price enforcement is available in the create-letter API; the user accepts applicable account charges.

The ledger commits a durable claim before HTTP, permits one POST, and permanently blocks another attempt after a crash, timeout or response mismatch. Reconciliation only reads the existing resource or its exact metadata filter. An empty result does not unlock sending. Keep this ledger and its backups: deleting it, using another copy/computer or manually editing its rows defeats local protection. This is not distributed account-wide deduplication. Never send the same request through another tool while an attempt is unresolved.

Signed provider asset URLs and raw response bodies are not stored. Saved receipts are minimal private evidence, not proof of delivery or correction. The approved addresses and letter are necessarily transmitted to Lob during an authorized submission; test requests also transmit their content. Use fictional data for qualification.

Contact routes and their history also contain private case associations. Public directory suggestions never establish household authority or provider scope. Only explicitly verified postal routes can prepare new advertising Lob letters. Changed/stale routes invalidate an unsubmitted preview’s approval path. Older unsubmitted advertising previews without a bound verified route cannot be approved or sent after migration. Attach the route, replace the unsubmitted preview and approve again. Already accepted or uncertain attempts remain locked and can still be reconciled without creating another send. Household kits are ordinary paper letters to current residents, not postal-service credentials or suppression orders.
