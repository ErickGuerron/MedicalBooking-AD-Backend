Objective: let each patient keep saved payment cards in Stripe and reuse them for checkout, with endpoints to add, list and remove them: GET /api/v1/me/payment-methods, POST /api/v1/me/payment-methods/setup-intents, DELETE /api/v1/me/payment-methods/{payment_method_id}.
Branch: feature/payment-methods, stacked on feature/rest-endpoints-faltantes (PR #15 not merged yet). Delivery: ask-on-risk. Test runner: uv run pytest -m "not integration" (unit); uv run pytest (full, SQL Server). Risk: item 2 (payment credentials and personal financial data: which customer a card belongs to, ownership on delete) and item 3 (new public routes; checkout response unchanged in shape).

## Specs
- S1: User decision (option A): saved cards through the checkout AND adding a card without paying (SetupIntent).
- S2: Naming in English. Path `/me/payment-methods` (not `/me/paymethods`). Existing `/me/perfil-clinico` is untouched.
- S3: One Stripe Customer per user, created lazily the first time the user needs it (checkout, setup intent). The mapping is stored in a new table `stripe_clientes` (usuario_id unique, stripe_customer_id unique, creado_en). Creation uses the Stripe Idempotency-Key `customer-<usuario_id>` so two concurrent first requests yield one customer.
- S4: The database stores no card data: no number, no CVC, no expiry, no full PaymentMethod copy. Listing reads Stripe. Only the Stripe customer id is persisted.
- S5: GET /api/v1/me/payment-methods returns {"items":[{"id","brand","last4","exp_month","exp_year","is_default"}]} for the caller's customer, cards only (type card). A user with no customer row gets {"items":[]} WITHOUT creating a Stripe customer and without calling Stripe. is_default reads the customer's invoice_settings.default_payment_method.
- S6: POST /api/v1/me/payment-methods/setup-intents creates a Stripe SetupIntent for the caller's customer (usage off_session) and returns {"client_secret","publishable_key"}. Stripe unconfigured or unreachable answers 503 and creates nothing locally.
- S7: DELETE /api/v1/me/payment-methods/{payment_method_id} detaches the card only when it belongs to the caller's customer: the server reads the PaymentMethod from Stripe first and compares its customer. Missing and foreign cards both answer 403 with the same body. Success answers 204.
- S8: POST /api/v1/payments/checkout keeps its current response shape (pago_id, client_secret, publishable_key). The session is created for the caller's Stripe customer (creating it if missing) with payment_intent_data[setup_future_usage]=off_session, so the embedded form offers to save the card. Idempotency behaviour of checkout is unchanged.
- S9: Permission `metodos_pago.gestionar` granted to PACIENTE by migration 0020 (sql file and alembic revision). Routes require it; any other role gets 403. Ownership is checked in the application layer.
- S10: Migration 0020 is additive: creates `stripe_clientes` and the permission. Idempotent (MERGE / IF NOT EXISTS), same pattern as 0018 and 0019.
- S11: Routes and models documented in OpenAPI (docs/api/openapi.json regenerated with json.dumps(indent=2, sort_keys=True, ensure_ascii=False)) and README rows. Tests pin each rule (one RED per rule), the gateway uses httpx.MockTransport like test_stripe_checkout_gateway.py, and integration tests cover the migration and the three routes against SQL Server with a stub gateway.
- S12: Stripe API shapes must be confirmed against the Stripe documentation (Context7 /websites/stripe) before coding: customers.create, setup_intents.create (usage, customer, automatic_payment_methods), customers/{id}/payment_methods, payment_methods/{id} retrieve and detach, checkout session customer and payment_intent_data.setup_future_usage. Do not guess field names.

## Tasks
- T1 | S3, S4, S10 | migration 0020 (sql + alembic) and stripe_clientes repository | done | commit: see PR
- T2 | S3, S5, S6, S7, S12 | gateway methods for customer, setup intent, list, retrieve, detach | done | commit: see PR
- T3 | S5, S6, S7, S9 | service use cases and routes under /me/payment-methods | done | commit: see PR
- T4 | S8 | checkout attaches the Stripe customer and off_session save | done | commit: see PR
- T5 | S11 | OpenAPI, README, openapi.json | done | commit: see PR
- T6 | S1..S12 | full verification (973 passed, 1 xfailed) and independent verifier (Risk item 2) | done | commit: see PR

## Log
- L1 (user, verbatim): "actualmente se peude guardar las tarjetas de cada cliente para que se utilice para el pago? y tambien que el usuario peuda guardar diferentes metodos de pagos que se leisten con un ednpoint /me/paymethods (mejoralo ya que debe estar en ingles"
- L2 (parent): answered: no card or customer is stored today (checked src/ and migrations).
- L3 (user, verbatim): "A"  (option A: saved cards through checkout AND SetupIntent to add without paying; path /me/payment-methods)
- L4 (verifier, read-only): FAIL on S6 (blocker): the customer mapping was stored before the SetupIntent call, so a failed SetupIntent left a local row. Fixed: CustomerDirectory.with_customer stores the mapping only after the provider action succeeds (test_payment_methods_setup_order.py, RED observed first). Also DELETE path changed to <path:payment_method_id> so a malformed id with slashes answers 403, not 404 (test_a_card_id_with_slashes_is_403_not_404; that RED was not observed separately). Advisories kept as decisions: no Idempotency-Key on SetupIntent; list reads only the first 100 cards; a checkout whose customer creation fails stays PENDIENTE with no session and is retried on the next request.
