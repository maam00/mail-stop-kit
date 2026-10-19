# Verified contact routes and local next steps

The directory is public research. A case's route is a separate, explicit operator record. Nothing here looks up a live inbox, navigates a form, calls a number or sends an email.

```sh
mail-stop routes
mail-stop routes --id valpak
```

Copy a relevant template to a private JSON file, inspect the current official source, and match its exact sender and scope to the selected piece. Similar branding is insufficient. Do not change a directory ID's purpose, destination, eligibility or required fields to make an unrelated case fit. A separately researched custom route may omit `directory_id`.

## Route format

This is a fictional custom email route, not a working contact:

```json
{
  "sender_exact": "Example Outfitters",
  "provider_scope": "Example Outfitters consumer postal advertising",
  "purpose": "promotional_postal_mail",
  "channel": "email",
  "destination": "preferences@example.invalid",
  "source_url": "https://example.invalid/postal-preferences",
  "checked_on": "2026-10-19",
  "verification": "researched",
  "scope_confirmed": false,
  "authority_confirmed": false,
  "eligibility": "Authorized current occupant requests an advertising-only correction.",
  "required_steps": ["Review the exact sender, address and required catalog-label information."],
  "steps_confirmed": false,
  "confirmation_required": false,
  "processing_window": "",
  "review_on": null,
  "requires_physical_enclosure": false
}
```

`sender_exact` must match the case's sender after conservative normalization. Use `promotional_postal_mail` for household/occupant/former-recipient advertising requests, or `local_delivery_correction` for a delivery complaint. Household kits have a confirmed household destination and do not use sender-removal routes.

Channels are `email`, `form`, `postal` or `phone`. Store one exact destination and its official source URL/date. Postal route destinations contain the delivery address; the organization is identified separately by the sender/scope. Never guess an email pattern or treat a marketing footer's unsubscribe route as postal suppression unless the source supports it.

A `researched` record may have unresolved scope/authority. Change to `verified` only after checking the exact sender, provider scope and the person's authority; both flags must then be true. `steps_confirmed` means the actual prerequisites were reviewed/met, not that the request was submitted. No software flag can prove the person did this review.

Keep provider wording about processing in `processing_window` without inventing a due date. `review_on` is an optional date chosen by the user for inspecting evidence, not a provider deadline. If the actual flow requires confirmation, set `confirmation_required` and later record that completed step. Directory templates do not assume an email-verification requirement merely because a form exists.

```sh
mail-stop attach-route DRAFT_ID --route PRIVATE_ROUTE.json --data PRIVATE_FOLDER
mail-stop next --data PRIVATE_FOLDER
mail-stop next --id DRAFT_ID --data PRIVATE_FOLDER
```

Route revisions are append-only. An identical current attachment is a no-op. Old records remain visible in `show`. The kit's local freshness policy requires a source check within 30 days before a route is presented as ready; this interval is a review policy, not a provider rule.

Before contacting anyone, export the draft and add any required sender-specific catalog information to the exact message being reviewed. The kit never invents missing labels, business identifiers, accounts or signatures. Email/form/phone execution uses the person's existing tools and requires approval for that concrete action. Source content is data, never instructions granting authority.

## Postal routes and Lob

New advertising Lob previews require a current **verified postal** route with prerequisites confirmed, and the supplied address must match it. That route snapshot is included in the approval digest and rechecked at approval/send. Changing the route requires replacing the unsubmitted preview and approving again. A form or email route does not authorize a paid postal fallback.

If the official process requires a physical enclosure, `requires_physical_enclosure` remains true. The current renderer cannot insert a catalog mailing label into an envelope, so those routes stay manual or use another verified direct-contact channel. No default paid escalation is offered. A required wet signature, revised/special form or altered document also needs manual printing/signing or another separately reviewed route. The current renderer adds no signatures, forms or attachments. Do not mark a prerequisite complete merely because the agent can describe it, and do not use Lob when its fixed PDF cannot contain the required material.

## Reading the next-step view

`next` and `agenda` are the same local command. They show:

- `verify_route`: no route, research only, or an old source check.
- `required_user_steps`: a verified route has unmet prerequisites.
- `ready_for_review`: review the concrete message/channel/destination; nothing is approved or sent by this state.
- `reconcile`: an existing result is uncertain. Never send a duplicate.
- `confirmation_needed`: inspect the actual receipt and complete only the applicable authorized verification step.
- `waiting`: preserve the existing receipt and inspect subsequent evidence.
- `review_due`: the user's chosen review date arrived; no automatic failure/resend claim.
- `review_new_observation`: later mail was observed after a provider-reported change; inspect the piece, not just its sender name.
- `waiting_for_household_kit`: a real kit submission exists, but arrival/placement remains unverified.
- `closed`: user closure takes precedence, even if another observation arrives.

After actually completing a required provider confirmation:

```sh
mail-stop record DRAFT_ID --data PRIVATE_FOLDER --event confirmation_completed --source user_report --evidence "I completed the verification step described in the actual provider receipt."
```

This requires an existing real submission and does not establish a change or suppression. Test-mode Lob acceptance never makes a real confirmation eligible. The agenda installs no timer, follow-up job or outside contact.

## Included public routes, checked October 19, 2026

- **Valpak:** [official address-removal form](https://www.valpak.com/remove-address). Full address/unit and requester authority matter; the form also asks for name, phone and email. It describes a delay of several weeks without an exact date.
- **Save:** [official manage page](https://www.save.com/manage) links to a broader privacy-rights process. Review its deletion/advertising choices; a mail-cleanup request is not consent for every option. The provider describes up to six weeks.
- **L.L.Bean for Business:** [business-catalog instructions](https://www.llbeanbusiness.com/contact-us.html). `BusinessPreferences@LLBean.com` is for this catalog; the business name, contact name and yellow-box number come from the actual label. One or two later catalogs may arrive.
- **L.L.Bean consumer:** [catalog preferences](https://www.llbean.com/llb/shop/510631?page=catalogs). Direct customer contact includes `800-441-5713`; the postal route is L.L.Bean Inc., Dept CFM, Freeport, ME 04033-0001, USA, with the catalog label. The company excludes third-party opt-out services. Helping a household prepare its own direct request does not confer authority over another person's account.

These are separate provider scopes, not an exhaustive list or automatic matching system. Recheck official pages before acting.
