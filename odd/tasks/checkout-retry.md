Objective: retry the Stripe Checkout Session creation for payment attempts that failed because the provider was unavailable, and expose the retry state to the app.
Branch: feature/checkout-retry (stacked on feature/payment-methods-hardening, PR #17; do not merge before #17).
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
T1 | S6 | route: inline (migration + repository) | status: pending | commit: -
T2 | S1, S2, S3, S8 | route: inline (domain rules in billing service) | status: pending | commit: -
T3 | S4, S5 | route: inline (one-shot worker, same pattern as expire_holds) | status: pending | commit: -
T4 | S4, S7 | route: inline (status endpoint fields, OpenAPI) | status: pending | commit: -
T5 | all | docs: README worker section, OpenAPI regen, backend-status | status: pending | commit: -
T6 | S1-S8 | verification: independent verifier (Risk: item 1 payments state, item 2 security-adjacent credentials handling: none beyond existing keys) | status: pending

Risk (draft): item 1 (changes state of payment attempts). Final Risk line set per commit.

## Log
L1 (user, 2026-11-11, verbatim fragments): "reintentar cada cierto tiempo" for checkouts that failed; "que solo sea por la app y se notifique dentro, ... para que se le notifique si hubo un error y el numero de intentos que va realizando, dentro de la app"; decision B: state stored on pagos, exposed through status; option 1: stack on feature/payment-methods-hardening.
L2 (parent): inspected code. Provider failure on create marks the attempt RECHAZADO (billing_service._open_session -> store.mark_rejected). Stripe 5xx on keyed POST is replayed, so retries need new keys (S3).
L3 (parent): no in-app notification UI exists in the front (Flutter, Angular); the front team must build the notice from the status fields.
L4 (user): "me parece mejor el reintentar ahorra, con los tiempos propuestos". Resolves S9 with the schedule above and the "Reintentar ahora" button. Constraint found: a seconds-level schedule needs a continuous worker, which breaks the one-shot pattern, so it was rejected.
