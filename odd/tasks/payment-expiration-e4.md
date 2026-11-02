Objective: E4 of the backend plan: a worker expires appointments still in PENDIENTE_PAGO past their hold, releasing the schedule slot, so unpaid holds cannot block availability forever.
Branch: develop. Delivery: ask-on-risk. Test runner: uv run pytest (integration needs SQL Server localhost,1433). Risk: item 4 (concurrency with booking and cancellation).

## Specs
- S1: User choice "E4: expiración de pagos (Recommended)" over E3 and deployment work.
- S2: E2 left PENDIENTE_PAGO holding the slot until cancelled, with expiration deferred to E4 (scheduling-e2 S8, accepted by the user).
- S3: Expiration sets citas.estado = EXPIRADA, sets horarios_disponibles.estado = DISPONIBLE, and writes history and outbox rows (cita.expirada, version 2) in the SAME transaction per appointment.
- S4: Only PENDIENTE_PAGO appointments whose expiracion_reserva is at or before now expire. CONFIRMADA, CANCELADA, EXPIRADA and future holds are never touched.
- S5: Safe with several concurrent workers and with concurrent cancel or booking: rows are claimed with UPDLOCK and READPAST, and each state change is conditional on the expected state. Re-running is a no-op.
- S6: The expiration runs as a separate one-shot process (python -m medicalbooking.workers.expire_holds) scheduled by the environment. Replicas stay stateless and never run background threads.
- S7: After expiration, a repeated booking with the same Idempotency-Key replays the EXPIRADA appointment without a new reservation; cancelling an EXPIRADA appointment answers 409.
- S8: Documentation: README and docs/deployment.md describe the worker command and its schedule. No new HTTP endpoint, so the OpenAPI contract does not change.
- S9: ASSUMPTION: the hold length stays APPOINTMENT_HOLD_MINUTES (default 15). Payment confirmation is not implemented yet; no appointment becomes CONFIRMADA in this feature.

## Tasks
- T1 | S3, S4 | inline | done | service use case expire_overdue_holds with batches; unit RED observed (4 failing: no method, no batch size), then GREEN (24 passed). Commit: 1ae6b50
- T2 | S3, S4, S5 | inline | done | SQL adapter _expire_once (UPDLOCK, READPAST, conditional UPDATE, history, outbox); integration tests incl. 4 concurrent sweeps and a locked-hold probe (tests/integration/test_scheduling_expiration.py). Commit: 1ae6b50 (risk item 4 named, own commit)
- T3 | S5, S7 | inline | done | integration: replay of EXPIRADA, cancel answers 409, slot reusable by another patient. Same file as T2
- T4 | S6 | inline | done | workers/expire_holds.py one-shot run()/main(); unit RED observed (import error), then GREEN (2 passed); smoke run against MedicalBooking_test: expired_holds=0 exit 0; failure path exit 1 with error type only. Commit: fd05fa0
- T5 | S8 | inline | done | README (worker section), docs/deployment.md (VM schedule). Docker command NOT run: needs Docker Desktop (not running). Commit: fd05fa0

Route: all inline. Verification: independent read-only verifier (gentle-ai-verify) because risk item 4 is named; ran once after the change checks.

## Log
- L1: "E4: expiración de pagos (Recommended)" (user answer after the question on the next backend block).
- L2 (evidence, baseline): uv run pytest -> 149 passed on develop before E4 changes.
- L3 (evidence, schema): CK_citas_estado allows EXPIRADA; history and outbox accept the same states; eventos_outbox needs version_agregado >= 1 unique per aggregate.
- L4 (evidence, final): uv run pytest -> 162 passed; ruff check and ruff format --check clean.
- L5 (evidence, verifier, read-only, risk item 4): no blocker. S3, S4, S5, S6, S7 met on the reviewed paths. Expire vs cancel is serialized by UPDLOCK, so cancel sees EXPIRADA and answers 409.
- L6 (advisory A1, fixed): a hold locked by another transaction is skipped by READPAST and expires on the next sweep. Pinned by test_sweep_skips_a_hold_locked_by_another_transaction.
- L7 (advisory A3, fixed): worker configuration errors print the exception type only (previously a traceback could echo the value). Smoke: APPOINTMENT_HOLD_MINUTES=abc -> exit 1.
- L8 (advisory A2, not fixed): expire and cancel lock citas then horarios; reserve locks horarios then inserts citas. Deadlock retry covers one collision; a second deadlock surfaces as ServiceUnavailable and the worker prints only that type. Not reproduced; revisit with load tests (spec 12).
- L9 (open, not verified): docs/deployment.md Docker command (`run --rm --entrypoint python migrate -m medicalbooking.workers.expire_holds`) needs Docker Desktop, which is not running. Pending with T7.
- L10 (assumption, S9): no CONFIRMADA path exists yet, so the expiration only affects PENDIENTE_PAGO holds today.
