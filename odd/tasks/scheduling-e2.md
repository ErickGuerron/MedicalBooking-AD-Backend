Objective: E2 of the backend plan: schedules and appointments (availability, booking with idempotency and no overbooking, cancellation) on SQL Server.
Branch: develop (uncommitted: E0/E1 work from earlier tasks is still pending review; E2 commits wait for that decision). Delivery: ask-on-risk. Test runner: uv run pytest (integration needs SQL Server localhost,1433).

## Specs
- S1: E2 acceptance (spec section 11): "2+ solicitudes simultaneas a mismo slot -> 1 exito y resto 409; idempotencia".
- S2: Spec 6: "GET /api/v1/availability", "POST /api/v1/appointments", "POST /api/v1/appointments/{id}/cancel" with "idempotency-key y concurrencia".
- S3: Spec 7.1: validate user, slot and idempotency key; conditional update of the free slot verifying exactly one affected row; appointment in PENDIENTE_PAGO plus history and outbox event in the SAME transaction; competing requests receive 409 CONFLICT without duplicate reservations.
- S4: Spec 12: "petición repetida con misma clave genera un único recurso y respuesta consistente; clave con payload distinto provoca rechazo".
- S5: Spec 12 concurrency: "20 peticiones paralelas sobre el mismo horario en SQL Server real; exactamente una reserva activa y sin pagos duplicados".
- S6: Spec 10: "Nunca compartir locks en memoria"; concurrency is resolved by the database.
- S7: Spec 6 authorization: patients book and cancel only their own appointments (citas.reservar; ownership enforced in the use case). Missing and forbidden appointments return the same 403 so identifiers do not leak.
- S8: Spec 7.1 step 6: payment must not stay inside a DB transaction; expiration and release are deferred to the payment and worker phase (E4). ASSUMPTION: PENDIENTE_PAGO holds the slot until cancelled; expiration is not implemented yet.
- S9: Spec 13 DoD: each endpoint has permission checks, audit relevance, unit and integration tests, error coverage and documentation.
- S10: The user asked for Scalar on the new endpoints: scheduling endpoints are part of the OpenAPI document and the Scalar page (scheduling_fragment). The contract is generated from the same Pydantic models that validate requests.

## Tasks
- T1 | S4, S6, S7 | inline | done | domain/policies.py: window validation, idempotency key format, same-request comparison, active states.
- T2 | S1, S3, S4, S7 | inline | done | application/ports.py and application/use_cases.py; unit tests with an in-memory store (tests/unit/test_scheduling_service.py).
- T3 | S3, S5, S6 | inline | done | infrastructure/repositories.py: one transaction per reservation and cancellation; conditional UPDATE; history and outbox rows.
- T4 | S2, S9 | inline | done | presentation/routes.py; wiring in app.py; setting APPOINTMENT_HOLD_MINUTES (default 15).
- T5 | S1, S4, S5, S7 | inline | done | tests/integration/test_scheduling_api.py: 11 tests, including 20 parallel bookings (one 201, 19 x 409, no residual history or outbox rows for losers).
- T6 | S8, S10 | inline | done | OpenAPI and Scalar cover the three scheduling endpoints (test_openapi_contract, 15 passed); README documents endpoints, configuration and export; docs/api/openapi.json regenerated. Payment expiration worker deferred to E4 (S8, user accepted this decision).

## Log
- L1: "ok, ahora si continua con la implementacion de los demas endpoints faltantes" (user request).
- L2: user choice: "E2: agendas y citas (Recommended)".
- L5 (evidence): uv run pytest -> 113 passed (E0-E2). ruff check and ruff format --check clean for src and tests.
- L6 (evidence): independent read-only verification (concurrency): no blocker. Advisories: IntegrityError is mapped broadly to 409; deadlock (1205) would surface as 500 (not reproduced); the isolation level was not inspected. Test gap closed (losers leave no history or outbox rows).
- L7: Risk: item 4 (concurrency). Verified by one independent read-only verifier plus the 20-parallel integration test.
- L8: Not committed. The backend repo has pending E0/E1 changes from earlier tasks; E2 work units need that decision first.
- L3 (evidence, schema): UX_citas_horario_activa is a filtered unique index on citas(horario_id) WHERE estado IN ('PENDIENTE_PAGO','CONFIRMADA'); citas.clave_idempotencia is globally UNIQUE; horarios_disponibles has UQ(agenda_id, inicio_utc) and a rowversion.
- L4 (evidence, schema): eventos_outbox requires version_agregado >= 1 unique per aggregate and payload as valid JSON.
