# Mongo projection (MONGODB outbox destination, E8)

Objective: project the same outbox events the Supabase replica receives into MongoDB Atlas, so both replicas hold the same information. Branch: feature/mongo-projection (from develop). Test runner: uv run pytest.

## Specs

S1. The MONGODB destination has a handler that applies each outbox event through the same projection mapping the Supabase replica uses (replica/application/projection_consumer.py). Only the sink differs.
S2. Each projected document is keyed by the aggregate id and stores the aggregate version (`_v`). An upsert only applies when the stored version is not newer than the event's version, so an older event never overwrites a newer state.
S3. Idempotence: an event id already processed by the same consumer is skipped. The processed mark is written after the projection succeeds, so a failed projection is retried and not marked done.
S4. Transient failures (database unavailable, timeout) raise TransientDeliveryError so the dispatcher retries. Invalid event payloads raise PermanentDeliveryError.
S5. The handler is wired into the dispatcher only when MONGODB_PROJECTION_URI is configured. Without it the MONGODB deliveries stay PENDIENTE, as today.
S6. Deletes are out of scope: the mapping has no delete events for users, appointments or orders yet (see the demo reset discussion).

## Tasks

T1 (S1-S3): sink with versioned upserts and processed marks. Test first.
T2 (S4): MongoOutboxHandler with error mapping. Test first.
T3 (S5): settings field and build_handlers wiring. Test first.

## Log

L1 (user, verbatim, 2026-11-13): "haslo"
L2 (plan approved in the preceding turn): replicate to MongoDB through the outbox so Azure SQL, Supabase and Mongo hold the same information; the demo reset needs delete events and is a later step.
