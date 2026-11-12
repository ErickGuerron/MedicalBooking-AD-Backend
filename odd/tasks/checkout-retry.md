Objective: retry the Stripe Checkout Session creation for payment attempts that failed because the provider was unavailable, and expose the retry state to the app.
Branch: feature/checkout-retry, rebased on develop after PR #17 was merged (43a7ee8) and tested by the user.
Delivery: ask-on-risk. Test runner: uv run pytest (SQL Server integration tests share one database; run serially).

## Specs
S1. Retry only the creation of the Stripe Checkout Session for payment attempts rejected because the provider was unavailable. Never retry the charge or webhook handling. (Source: "en ese caso entonces que solo sea por la app", "es la creacion de la sesion de pago".)
S2. At most 5 retries per payment attempt. (Source: "solo despues de realizar los 5 reintentos".)
S3. Each retry uses a new Idempotency-Key. Stripe replays the first answer of a keyed POST, including 5xx, so a failed key cannot be reused.
S4. No email. The patient is informed inside the app: the payment status exposes the number of attempts made and the last error code. (Source: "se notifique dentro, si es la creaccion de la sesion de pago no me parece tan relevante para envio de email".)
S5. The retry runs as a one-shot worker in medicalbooking/workers, executed by an external scheduler, never inside an API replica (same pattern as expire_holds).
S6. Retry state lives on the payment attempt (pagos): retry count, last error code, next retry time. No notificaciones rows and no channel column.
S7. The app never stores a checkout link. When the patient opens a pending payment, the app asks for a session through POST /payments/checkout, which creates a fresh session with a new key.
S8. ASSUMPTION: after 5 failed retries the attempt stays RECHAZADO with the exhausted state visible in the status, and the patient can start a new checkout attempt from the app.
S9. Retry schedule, decided by the user: the worker runs every minute and retries an attempt when its next retry time has passed. Gaps between retries: 1 min, 1 min, 2 min, 2 min, 5 min (about 11 min in total). The patient can also press "Reintentar ahora" in the app, which creates a fresh session immediately (same path as S7), without waiting for the worker.

## Tasks
T1 | S6 | route: inline (migration 0021, schema tests) | status: done (RED 3 failing, GREEN 24 passing; full suite 1023 passed, 1 xfailed) | commit: see git log
T2 | S1, S2, S3, S8 | route: inline (domain rules in checkout_retry.py, billing service, store) | status: done (unit RED 4 failing then GREEN; full suite 1033 passed, 1 xfailed) | commit: see git log
   Behavior change (intended): a failed provider create keeps the attempt PENDIENTE with the next retry scheduled, instead of RECHAZADO. Tests 'provider failure marks rejected' and 'new key after rejected opens new attempt' were replaced by the retry expectations. The store integration tests were written after the SQL, so they are regression checks, not RED evidence.
   Open before T6: differential check of shared checkout paths (_open_session, _replay, _reopen_pending) against the base commit, in a mktemp scratch copy.
T3 | S5, S9 | route: inline (retry_due_checkouts in billing service, worker workers/retry_failed_checkouts.py) | status: done (unit + SQL integration; suite 1047 passed, 1 xfailed) | commit: see git log
T4 | S4, S7 | route: inline (GET /api/v1/invoices/{factura_id}/checkout-status, OpenAPI, reintentar_ahora documented) | status: done (unit + HTTP integration; suite green) | commit: see git log
T5 | all | docs: README (worker section, endpoints, reintentar_ahora), docs/backend-status.md, OpenAPI regenerated | status: done | commit: see git log
T6 | S1-S9 | verification: TDD + SQL integration tests (native review skipped by user decision); differential check of shared checkout paths against base f70949c | status: done (suite 1054 passed, 1 xfailed; test_billing_api.py 6/6 on base and on HEAD) | commit: see git log

Risk (draft): item 1 (changes state of payment attempts). Final Risk line set per commit.

## Log
L1 (user, 2026-11-11, verbatim fragments): "reintentar cada cierto tiempo" for checkouts that failed; "que solo sea por la app y se notifique dentro, ... para que se le notifique si hubo un error y el numero de intentos que va realizando, dentro de la app"; decision B: state stored on pagos, exposed through status; option 1: stack on feature/payment-methods-hardening.
L2 (parent): inspected code. Provider failure on create marks the attempt RECHAZADO (billing_service._open_session -> store.mark_rejected). Stripe 5xx on keyed POST is replayed, so retries need new keys (S3).
L3 (parent): no in-app notification UI exists in the front (Flutter, Angular); the front team must build the notice from the status fields.
L5 (parent, T2): retry counter counts retry calls only; the first failed create sets the schedule with gap 1 min; retry n uses key "<clave>:<n>"; a manual "Reintentar ahora" counts as a retry (assumption, confirm with user). Risk: item 1 (changes payment attempt state transitions). Independent verifier deferred to T6 (one run at feature end).
L4 (user): "me parece mejor el reintentar ahorra, con los tiempos propuestos". Resolves S9 with the schedule above and the "Reintentar ahora" button. Constraint found: a seconds-level schedule needs a continuous worker, which breaks the one-shot pattern, so it was rejected.
L6 (parent, T3): worker runs retry_due_checkouts(50) once per schedule; a due attempt whose invoice is no longer pending is cancelled; a customer failure during a scheduled retry counts as a failed retry (no endless loop). Retries use derived keys, so overlapping runs converge. build_billing made public in app.py for the worker. Native review skipped by user decision; verification is TDD + integration tests.
L7 (parent, T4): the app had no way to read attempt state, so GET /api/v1/invoices/{factura_id}/checkout-status now returns estado, reintentos_realizados, reintentos_maximos, reintentos_agotados, ultimo_error_codigo and proximo_reintento_en. Same ownership rule as checkout (foreign and missing give the same 403); an invoice with no attempt answers 404. docs/api/openapi.json regenerated.
L8 (parent, T5-T6): docs updated. Differential: tests/integration/test_billing_api.py run against base f70949c and HEAD: 6 passed on both, so the shared happy path, replay, key-reuse and no-key 503 behave the same. Open items: manual 'Reintentar ahora' clicks each spend a retry (no cooldown); PR #17 must merge before this branch merges; the native review was not run for this candidate (user decision).
L9 (user, approved): cooldown for the manual 'Reintentar ahora': allowed once a minute after the
last failure, derived from proximo_reintento_en minus the gap that followed it (no new column).
A sooner request answers 409 and calls no provider. Tests: tests/unit/test_checkout_retry_cooldown.py,
HTTP test updated in tests/integration/test_checkout_retry_api.py.
