# Recording outcomes

The ledger records evidence supplied by its operator. Manual records do not authenticate a receipt or verify that physical mail stopped. The optional Lob adapter separately contacts Lob only when explicitly requested. A provider-message label means you supplied that message, not that the kit independently checked it.

`mail-stop record DRAFT_ID --data PRIVATE_FOLDER --event EVENT --source SOURCE --evidence "Short factual note"`

| Event | Suitable source | Meaning |
|---|---|---|
| `submitted` | `user_report`, `provider_message` | A real submission is reported or evidenced. Use this for actions outside the kit; adapter receipts have their own history. |
| `uncertain` | `user_report`, `local_note` | The outcome of a possible real submission is unknown. Reconcile the existing attempt before retrying. |
| `test_submitted` | `provider_message`, `local_note` | A sandbox/test-mode result. It cannot establish a real acknowledgment or change. |
| `acknowledged` | `provider_message` | The provider acknowledges the request. Requires a recorded real submission. |
| `change_reported` | `provider_message` | The provider says it made a change. Requires a recorded real submission; still not verified mail reduction. |
| `confirmation_completed` | `user_report`, `provider_message` | A specific provider verification step was completed after a real submission. It does not change the case into a success state. |
| `mail_observed` | `user_report`, `selected_envelope`, `selected_digest` | Another piece was observed; include `--observed-on YYYY-MM-DD`. A digest image does not establish physical delivery. |
| `closed` | `user_report` | The user is done with the request, regardless of outcome. |

Use `--reference` for a non-secret message, case, or provider ID. References are never fetched. Do not paste credentials, private access URLs, SSNs, signatures, or a whole inbox message.

Examples after an actual submission outside the kit:

```sh
mail-stop record DRAFT_ID --data PRIVATE_FOLDER --event uncertain --source local_note --evidence "Form result was unclear; no retry made."
mail-stop record DRAFT_ID --data PRIVATE_FOLDER --event submitted --source provider_message --evidence "Located the existing receipt; did not resubmit." --reference "case-example-123"
mail-stop record DRAFT_ID --data PRIVATE_FOLDER --event mail_observed --source selected_digest --evidence "A selected digest showed another envelope; physical receipt unverified." --observed-on 2026-10-19
```

The same event/evidence is recorded once. An additional submission after a recorded submission is refused; preserve the original attempt's history. A provider's reported change cannot be downgraded to acknowledgment by a later import. Closing never means suppression; later observations can be recorded without reopening the case.

`latest_mail_observation` uses the observation date, not the order older digests were imported. The summary deliberately keeps `mail_reduction_verified: false`. Measuring a change in volume needs a known observation window and coverage; selected examples do not provide that denominator. No timer silently declares success.

## Lob mailing history

`show` includes `lob_mailings` alongside the manual-event summary; `lob-status MAILING_ID` gives the address/service manifest, immutable digest, minimal receipt and audit history. The summary combines manual and native evidence without fabricating manual receipt events. A matched live Lob resource establishes `submission_recorded` and permits a later sender `acknowledged` or `change_reported` event. A test resource establishes only `test_only`; an interrupted test is `test_submission_uncertain`. Native attempts cannot be relabeled with a manual submission event. Use `lob-reconcile` for their uncertainty, and never promote a test-mode object to a real submission.

Lob states are `prepared`, `approved`, `submitting`, `uncertain`, and `accepted`. `submitting` after an interruption means acceptance is uncertain, not that it is safe to retry. `accepted` in test mode means no physical mail or charge. Live `accepted` means a matching letter resource exists at Lob, including resources whose rendering later failed; inspect the saved provider status. It does not mean printing, postal handoff, delivery, or correction. Render failure still does not authorize a duplicate.

Use `next`/`agenda` for a local view of what remains. A user-chosen review date is not a provider deadline or an automatic retry instruction. Provider processing-window text is retained without converting it into a guaranteed date. See [routes](routes.md).
