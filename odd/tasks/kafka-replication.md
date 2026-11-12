# Kafka replication: outbox to Kafka to Supabase and MongoDB Atlas (E8)
Objective: replicate outbox events from SQL Server to Supabase and MongoDB Atlas through one Kafka topic, with independent consumers.
Branch: feature/kafka-replication (stacked on feature/soap-notifications-outbox, PR #20, commit 66f6c1c). Delivery: ask-on-risk. Test runner: uv run pytest (MEDICALBOOKING_TEST_DATABASE per worktree).

## Specs
S1. Flow: SQL Server outbox (eventos_outbox) -> dispatcher -> one Kafka topic `medicalbooking.events`. Each event is produced with the aggregate id as key, so order holds per aggregate. A delivery is marked KAFKA only after the broker acknowledges (acks=all, idempotent producer).
S2. Supabase replica: a consumer in consumer group `replica-supabase` reads the topic and writes read-only projections of orders and results, idempotently by event id. It never writes back to SQL Server. Supabase only serves contingency queries (spec section 8.3).
S3. MongoDB Atlas replica: a consumer in consumer group `replica-mongodb` reads the same topic and writes sanitized documents (events and audit), idempotently by event id (upsert). No personal data beyond need, no clinical values in email-like payloads (spec section 8.3).
S4. Independence: the two consumer groups progress separately. An outage in one never blocks or rewinds the other. Kafka retains events so a stopped consumer resumes from its offset. Scope note: if SQL Server is down, the dispatcher cannot read new outbox events, so nothing new is published. Kafka does not protect against SQL outages.
S5. Prototype broker: a single KRaft broker container on VM1 (deploy/vm1/compose.yaml). KAFKA_ADVERTISED_LISTENERS is the VM address the clients use. Log volume persisted. JVM heap bounded. Port 9092 is not exposed to the public load balancer. No high availability in this prototype (documented limit: one node, no quorum).
S6. Tests: unit tests for event-to-message mapping and idempotent writes with fakes for Kafka, Supabase and Atlas. Integration test against a real Kafka container for produce acknowledgement and consumer offset. No test needs Atlas or Supabase credentials.
S7. Documentation: help text of each new worker, and docs/kafka-replication.md with topology, topics, consumer groups and the single-broker limit.

## Tasks
T1. Dispatcher publishes to Kafka (S1, S4 partly). Route: delegated writer (unit A). Status: pending. Commit: -
T2. MongoDB consumer (S3, S6). Route: delegated writer (unit B, parallel with C). Status: pending. Commit: -
T3. Supabase consumer (S2, S6). Route: delegated writer (unit C, parallel with B). Status: pending. Commit: -
T4. Broker in compose and deployment docs (S5, S7). Route: inline (parent). Status: pending. Commit: -
T5. Seam check: merge the units, run the full suite once, Risk line per commit. Route: inline (parent). Status: pending. Commit: -

Route declaration: T2 and T3 are independent consumers with disjoint edit surfaces, launched together after T1 defines the message contract.
Risk: T1 item 5 (new delivery dependency: Kafka client library) and item 3 (new message contract consumed by others); T2 item 2 (Atlas credentials); T3 item 2 (Supabase credentials).

## Log
L1 (user, verbatim): "ok continua pero estamos deaceurdo que de sql server a outbox y se replcia en las dos bases al mismo tiempo, ese es el flujo y el outbox es con kafka"
L2 (user, verbatim): "ok continua"
L3 (parent): flow confirmed by the user: SQL Server -> outbox -> Kafka -> Supabase and Atlas at the same time. Earlier discussion of a chain SQL -> Supabase -> Mongo was rejected (Mongo would depend on Supabase, which pauses on the free tier).
L4 (parent): spec section 1 amended in MedicalBooking_Especificacion_Backend.md: Kafka single-broker prototype is in scope; high-availability cluster stays out.
L5 (parent, open): Supabase connection variables (SUPABASE_URL, SUPABASE_SECRET_KEY) are not verified yet; needed before T3 integration tests.
