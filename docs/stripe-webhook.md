# Stripe webhook: sandbox and production setup

Status: **receiver implemented** (E4, payments): `POST /api/v1/payments/webhook` verifies the signature, records each event once and applies the checkout change. The checkout session creation endpoint (`POST /api/v1/payments/checkout`) is not implemented yet, so until it exists no payment row matches and real events are recorded as `IGNORADO`. This document covers how the webhook is created in Stripe, independently of the application code.

## 1. Concepts

- **Test mode and live mode are separate.** Each has its own API keys, its own webhook endpoints, and its own signing secrets. A test endpoint never receives live events.
- **Each webhook endpoint has its own signing secret** (`whsec_...`). If the same URL is registered in test and live, the two secrets differ. Store each one under its own name.
- **The signing secret is not the API secret key.** `STRIPE_SECRET_KEY` calls the Stripe API. `STRIPE_WEBHOOK_SECRET` verifies that a request came from Stripe.
- **Payments are confirmed only by a verified webhook** (specification section 11, E4). The browser redirect after checkout is not proof of payment.

## 2. Configuration names

| Variable | Contents | Where it lives |
|---|---|---|
| `STRIPE_SECRET_KEY` | `sk_test_...` locally, `sk_live_...` in production | `.env` (local, not versioned) or the production secret store |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` of the endpoint for that mode | Same as above |

Do not commit real values. `.env.example` gets placeholders only, added when the payments code lands.

## 3. Sandbox (test mode): independent of the application

Use test mode for all development and for the E4 evidence. Test mode needs no real money.

### Option A: Stripe Dashboard

1. Sign in at https://dashboard.stripe.com and confirm the **Test mode** toggle is on.
2. Go to **Developers > Webhooks > Add endpoint**.
3. Endpoint URL: a public HTTPS URL that reaches the API. For a quick test you can use a tunnel to `127.0.0.1:5000`; Stripe requires HTTPS for endpoints created in the Dashboard.
4. Events to send: the checkout events listed in section 5.
5. Save, then **Reveal** the signing secret and store it as `STRIPE_WEBHOOK_SECRET` (test value).
6. **Send test webhook** from the endpoint page to check delivery.

### Option B: Stripe API (scriptable, repeatable)

Run once with the test secret key. Do not run it with `sk_live_...` by mistake.

```powershell
curl.exe https://api.stripe.com/v1/webhook_endpoints -u "sk_test_...:" -d "url=https://<public-host>/api/v1/payments/webhook" -d "enabled_events[]=checkout.session.completed" -d "enabled_events[]=checkout.session.async_payment_succeeded" -d "enabled_events[]=checkout.session.async_payment_failed" -d "enabled_events[]=checkout.session.expired"
```

Use `curl.exe`, not `curl`: in Windows PowerShell, `curl` is an alias for `Invoke-WebRequest`. Keep it on one line.

The response contains `secret` (`whsec_...`). That value is shown only at creation time for this endpoint. Save it immediately. If it is lost, delete the endpoint and create a new one, or roll the secret from the Dashboard.

### Option C: local development with the Stripe CLI

The CLI does not need a Dashboard endpoint. It forwards test events to your machine.

```powershell
stripe login
stripe listen --forward-to http://127.0.0.1:5000/api/v1/payments/webhook --events checkout.session.completed,checkout.session.async_payment_succeeded,checkout.session.async_payment_failed,checkout.session.expired
```

Write the command on one line. PowerShell does not continue lines with a trailing `\` (that is bash syntax). If you must split it, end each line with a backtick (`` ` ``) in PowerShell.

The command prints a signing secret. Use it as `STRIPE_WEBHOOK_SECRET` for local runs only.

- The CLI secret is for the CLI session. It is not the secret of a Dashboard endpoint, and the two are not interchangeable.
- To print the secret without forwarding: `stripe listen --print-secret`.
- To send a test event: `stripe trigger checkout.session.completed` in a second terminal.

## 4. Production (live mode)

Do this only after the sandbox flow passes end to end and the public HTTPS deployment is working.

### Prerequisites

- The Stripe account is activated for live payments (business details, bank account, identity checks completed in the Dashboard).
- The API is reachable at a public HTTPS URL with a valid certificate. The deployment plan currently lists HTTPS with a DuckDNS name and Let's Encrypt as a later step (`docs/deployment.md`), so this is a blocker for production.
- Nginx (or the Azure Load Balancer in front of it) routes `/api/v1/payments/webhook` to a healthy API instance and does not require a JWT on that path. Stripe cannot send a JWT; the endpoint is authenticated by signature verification instead.
- The live secret key and the live webhook secret are stored in the production secret store, not in the repository.

### Steps

1. In the Dashboard, switch the toggle to **Live mode**.
2. Go to **Developers > Webhooks > Add endpoint**.
3. Endpoint URL: `https://<production-host>/api/v1/payments/webhook`.
4. Select the same events as sandbox (section 5).
5. Save and **Reveal** the signing secret. Store it as the production `STRIPE_WEBHOOK_SECRET`.
6. Confirm the production `STRIPE_SECRET_KEY` is the live key and that it is not the test key.
7. Send a test webhook from the Dashboard endpoint page. Confirm the API returns 2xx and the event is recorded in `stripe_eventos`.
8. Do not run `stripe trigger` against production. It is for test mode.

### Rotation

If the signing secret is exposed, roll it from the endpoint page in the Dashboard, update `STRIPE_WEBHOOK_SECRET` in the secret store, and restart the API instances. Old requests signed with the previous secret will fail verification after the change.

## 5. Events

Checkout events for the E4 flow:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.async_payment_failed`
- `checkout.session.expired`

Subscribe only to events the code handles. Confirm the exact event names against the Stripe event types reference when the handler is written. Unknown events are acknowledged with 2xx and recorded as `IGNORADO` in `stripe_eventos`.

## 6. Receiver requirements (for the implementation)

- Read the **raw request body** before any JSON parsing. Signature verification fails if the body is re-serialised. In Flask, use `request.get_data()`.
- Verify the `Stripe-Signature` header with the endpoint secret. Reject invalid signatures with 400 and write no state.
- Deduplicate by `event.id` using the unique column `stripe_eventos.stripe_event_id`. Stripe retries delivery, so the same event can arrive more than once.
- Return 2xx quickly and do the slow work after the response, or in the worker (E4 outbox), so Stripe does not time out and retry.
- Never log the full payload with card or customer data, and never log the signing secret.

## 7. Verification checklist

- [ ] Test endpoint created; `STRIPE_WEBHOOK_SECRET` is the test value
- [ ] Invalid signature returns 400 and changes no row
- [ ] Valid `checkout.session.completed` marks the payment as confirmed exactly once
- [ ] Re-sending the same event does not duplicate the payment (dedupe)
- [ ] Production endpoint exists only in live mode, with its own secret
- [ ] No secret appears in the repository, logs, or test output
