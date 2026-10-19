# Working on Mail Stop Kit

Read README.md, SKILL.md and the relevant docs before changing behavior. This is a small local extraction of existing physical-mail cleanup work, not a hosted-service restart.

Use only synthetic data and temporary ledgers in tests. Do not inspect or import a connected household archive, keys or inbox to develop this repository. Never add real names, addresses, envelope scans, signatures, receipts or tokens to fixtures. Keep the review core dependency-free. The optional Lob extra uses ReportLab for local PDFs. Do not add hosted accounts, recurring jobs or other integrations.

Recipient role, mail purpose, address review and action eligibility are distinct. Unknown is not former resident. Current Resident wording wins. Keep household-kit delivery separate from local-office letters: current names on the persistent note, confirmed former names only on the carrier copy, ordinary letter to the current household. Never infer a former-resident role or disable valid Current Resident delivery.

Routes are explicitly matched, versioned local evidence; directory research is not case verification. Preserve provider scope, required steps, contact-channel choice and the route snapshot bound to a Lob approval. The next-step view never authorizes automatic actions. Preserve protected notices, original observations and the separation between test acceptance, submitted, acknowledged and provider-reported changes. No outcome here proves mail reduction.

Current authorization to edit/test this kit does not itself authorize provider actions, paid sends or publication. Only explicit `lob-send` and `lob-reconcile` may contact the fixed Lob API. Developing or testing this adapter never authorizes using a real key or making a provider request. Recheck official procedures before changing playbooks and include source/date/limits. Preserve exact-PDF approval, current account price consent, test/live separation, durable pre-HTTP claims and permanent uncertain-attempt locks. Never retry an ambiguous POST or use a new ledger/ID as a workaround.

Install `.[lob]` and run `.venv/bin/python -m unittest discover -s tests -v` so adapter tests are not skipped. Check installation and packaged playbooks when packaging changes. No credentials or provider calls are needed. Keep the standalone candidate separate from the original Mailroom installation.
