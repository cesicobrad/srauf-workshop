# Gmail + built-in customer records

**HubSpot isn't required.** Customer records are built into the workshop desk and persist on this device. Requests from the same email share a customer summary, including enquiry, accepted-quote and delivered-message counts. Photos, quotes, assignments, deadlines, replies, follow-ups and CSV export all work without an external CRM account. Existing data is preserved.

The running demo uses local preview delivery. Gmail sending and inbox import can be enabled independently; **Gmail has not been authenticated or live-verified**. No website, HubSpot signup or HubSpot identity verification is needed to run this application.

## Gmail

Use a test mailbox and another inbox you control. Authorize a Google OAuth client for Gmail with `https://www.googleapis.com/auth/gmail.send` for outgoing messages and `https://www.googleapis.com/auth/gmail.readonly` for the optional inbox importer. The importer does not require mailbox modification permission. Obtain a refresh token through Google's consent flow and place it with the OAuth client ID and client secret in `.env`. A short-lived access token is also supported for a one-session test.

```dotenv
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REFRESH_TOKEN=
GMAIL_FROM=
GMAIL_TO_ALLOWLIST=
GMAIL_INBOUND_ALLOWLIST=
GMAIL_LABEL=WorkshopDemo
```

Set `GMAIL_FROM` to the authorized sender. Put only addresses you control in the comma-separated recipient and incoming-sender allowlists. Create the dedicated **WorkshopDemo** label in Gmail and apply it only to test messages. Outgoing-only mode needs the first allowlist; inbox import needs the second.

```powershell
node scripts/start.mjs --live-gmail --gmail-inbox
```

The importer checks up to 20 recent labelled messages once per minute (within seven days). It leaves read/unread state unchanged, imports plain text only, and skips automatic reports. Attachments from email are not imported; the web enquiry form supports photos. Repeated Gmail message IDs are ignored. A reply must match a saved outgoing Message-ID and the enquiry's sender email before it can update that enquiry and cancel follow-ups. Unmatched reply references are skipped. Sender matching is intended for an allowlisted test inbox; it is not a verified sender-authentication system for public deployment.

Outgoing messages use the exact reviewed outbox text and a stable Message-ID. If submission loses its confirmation, the job stops as uncertain. Check Gmail Sent for that Message-ID before manually resolving it. A Gmail API acceptance alone does not prove final inbox receipt.

Google references: [send a message](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send), [list messages and search query](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list), [get a message](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get).

## Optional HubSpot adapter (unused)

Skip this section for the current demo. The adapter is retained for a future client who already has an authorized account. It is hidden in the interface unless the runtime is explicitly launched with `--live-crm`. It has only been tested with simulated provider responses.

For such a future integration, obtain an access token from a HubSpot app authorized to read and write contacts in that account. Configure the contact read/write scopes requested by the app (`crm.objects.contacts.read`, `crm.objects.contacts.write`). Put the token and a comma-separated allowlist of test contact email addresses in `.env`:

```dotenv
HUBSPOT_ACCESS_TOKEN=
HUBSPOT_EMAIL_ALLOWLIST=
```

```powershell
node scripts/start.mjs --live-crm
# Combined mode after both authorizations:
node scripts/start.mjs --live-gmail --gmail-inbox --live-crm
```

New enquiries queue a CRM task when that mode is enabled. Existing enquiries have a **Sync allowlisted test contact** button on the desk. The third actual n8n workflow calls the backend, which checks the allowlist and upserts the contact by email. Only email, first name and last name are mapped. Provider-confirmed contact IDs and n8n execution IDs appear in the local audit. This does not create deals, pipelines, bookings or email subscriptions. Retrying an unconfirmed upsert uses the same contact email.

HubSpot reference: [contacts guide and batch upsert](https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/contacts/guide).

## Confirm the connections

1. Stop the preview launcher, fill the local `.env`, and restart with the relevant flags. Flags default off on every launch.
2. Submit a fictional enquiry using allowlisted inboxes you control. Verify the saved customer record on the workshop desk. HubSpot verification is unnecessary for the current setup.
3. Review and approve a test reply. Verify the exact body in the receiving inbox, rather than relying on the local status alone.
4. Reply to that message, label the incoming reply WorkshopDemo and wait for polling. Confirm that it appears on the existing enquiry and suppresses pending follow-ups.
5. Record only those completed provider checks as live-verified. Until then, keep the current pending/unverified description.

The preview clock and delivery-failure simulation are disabled when any live account mode is enabled. Existing `.example` customers are not allowed recipients unless explicitly placed in an allowlist, and those domains cannot receive mail. Keep the app local; public use needs proper staff/customer authentication and provider onboarding.
