# Owner handover · Šrauf demonstration

## Review a request

Open **Workshop desk**. Select a customer. Read the question and expand the conversation to see what was already discussed. The draft is a starting point: templates in offline mode, generated text only when live AI is explicitly enabled.

Check the recipient, subject and body. Confirm any price or appointment yourself. Edit the text, then approve. The preview button sends only to the local preview inbox; SMTP mode has a different label. Rejecting sends nothing.

Two people reviewing the same draft cannot silently overwrite one another. If it changed, refresh and review the current version before approving.

## Recover a failed job

The enquiry remains saved even when n8n is down. A failed delivery retains the exact approved text in its outbox entry.

In the preview scenario, restore delivery, open the failed enquiry and choose **Retry the saved job**. You do not need to retype or approve a new copy. The audit trail records dispatches and n8n execution IDs.

If the item says **Check email provider**, do not resend blindly. The external server may already have accepted it. Use its Message-ID to check the provider's sent log. This demo deliberately has no automatic retry button for that state.

## Photos, assignment and quotes

Customers can attach up to three JPEG/PNG/WebP photos of 4 MB each. The example button attaches a labelled illustration. Staff can open the saved images from the enquiry; these are for a mechanic, not AI diagnosis.

Choose the assigned person, next action and due time, then **Save assignment**. Filter the queue by owner, work needing action or overdue work. Overdue alerts are saved once per deadline. Acknowledge an alert to clear its notice; change the due time or complete the work to resolve the overdue work itself.

In **Prepare a repair quote**, enter labour/parts, quantities, final unit amounts and turnaround. **Save quote for review** creates a version without sending it. Check its saved total and notes, then approve delivery. The quote uses the same durable outbox and actual n8n delivery workflow as replies.

In Customer view, the delivered quote offers **Accept demo quote** and **Ask the workshop**. A question returns to the desk. Prepare a new revision when details change; the old revision becomes superseded and cannot be accepted. Acceptance sets **Schedule repair** as the next action. It does not charge or book anything. Expired quotes need a new revision. A quote cannot be replaced while delivery is running or uncertain, or after acceptance.

## Improve an unanswered question

Open **Improve answers**. Select an unanswered question, write only a workshop-approved answer and publish it. **Test this question now** calls the actual chat API and shows its answer/source. Offline mode supports that exact question with case/punctuation normalization; live AI may retrieve it as a source. Uploaded photos and customer text never automatically become approved facts.

## Customer records and optional Gmail

Customer records are built into the desk and saved automatically. The customer summary groups enquiries by email and shows accepted quotes and delivered messages. Export the enquiry list as CSV when needed. No HubSpot account is required.

Gmail can independently handle outgoing messages and labelled incoming replies after authorization. See [the connection guide](CONNECTIONS.md). The current preview sends no external email. The old HubSpot adapter is hidden and unused unless explicitly enabled for a future integration.

## Change prices and hours

Use **Owner controls** on the workshop desk. Edit the prices, translated opening hours and repair policy, then publish. New questions use the new version immediately. Existing conversations preserve their earlier answers.

The conflicting-leaflet button is an explicit scenario switch, not an automatic document-conflict detector. It marks a hypothetical unreviewed leaflet as conflicting. Resolve it by confirming and publishing the owner facts. No arbitrary uploaded-document audit is claimed.

## Follow-ups

A follow-up becomes eligible three days after the initial delivered reply, provided the customer has not replied. It is a new draft requiring approval, not an automatically sent message. Rejected follow-ups are not repeatedly regenerated.

In preview mode, **Advance demo clock 3 days** makes this visible without a real wait. The banner always shows the clock offset. In the preview inbox, send a customer reply to cancel pending follow-ups. A reply cannot recall an email that has already been handed to an external provider.

## Pause and export

**Pause automation** stops new job dispatches while retaining enquiries. Resume it to continue. It does not recall a job already running in n8n.

**Export enquiries** downloads a CSV of the current ledger. It is a local export, not proof of an external CRM integration.

## Stop and restart

Stop the launch terminal with Ctrl+C. Restart with `node scripts/start.mjs`. SQLite data stays in `data/`; n8n's separate database and bridge configuration stay in `.runtime/`.

After an interrupted external send, check the provider before retrying. A claimed local job is eligible for recovery after its 90-second lease expires.

Never publish `data/`, `.runtime/` or `.env`. Current test data is fictional, but real use would require appropriate access controls and retention settings before collecting customer information.
