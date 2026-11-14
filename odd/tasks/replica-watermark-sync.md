Objective: every 15 min, sync into Supabase and MongoDB the Azure SQL rows written outside the API (external SQL inserts, and writes made while the replicas were down). Project: practica_azure (medicalbooking-backend, Medilo DB on Azure).
Branch: feature/replica-watermark-sync (from feature/replica-reconciliation 988d36f). Delivery: ask-on-risk. Test runner: uv run pytest tests/unit (never integration against the production .env). Env: load with %TEMP%\load_env.ps1 before any script; without it, scripts target local DB MedicalBooking.

## Specs
S1. Every 15 min, read usuarios and citas rows whose version_fila (CAST AS BIGINT) is greater than the stored watermark, and queue them for the replicas through the outbox (MONGODB, KAFKA, SUPABASE via Kafka consumer).
S2. The watermark is stored per table in Azure SQL (sincronizacion_watermarks: tabla, ultima_version BIGINT, actualizado_en). It advances in the same transaction that queues the batch; the dispatcher handles delivery retries.
S3. A first run with no watermark starts at 0 and covers the full history (decision confirmed by the user).
S4. Hard deletes are not visible to the watermark. Phase 2: compare identifiers across Azure and the replicas.
S5. No direct writes to MongoDB or Supabase from the worker; the outbox delivers.
S6. The worker never prints connection strings, payloads, or row contents; counts and error types only.
S7. Re-running the worker with no new rows changes nothing.

## Tasks
T1. Watermark store + migration 0028 (sincronizacion_watermarks), unit tests. Status: done (commit e7069b4). Migration APPLIED on Medilo (alembic 0028_sincronizacion_watermarks = head).
T2. Changed-row reader for citas, unit tests. Status: done (commit b43e4b3).
T2b. Changed-row reader for usuarios (roles and patient profile in the payload). Status: PENDING.
T3. Worker sync_external_writes + publisher + run_cycle, unit tests. Status: done (commit d8598a0). Full unit suite: 1031 passed, 1 skipped.
T4. Docs: docs/replica-watermark-sync.md (merged). Status: done for the design; the scheduled deployment section is pending (see P1).
T5. Apply migration 0028 on Medilo. Status: done (verified: table exists, 0 rows initially).

## Verified end-to-end (Medilo, Azure)
- Full history: queued=6, watermark citas=2350 = max version; rerun queued=0.
- Dispatcher with KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:9092: citas events 18 ENTREGADO on KAFKA, MONGODB and NOTIFICACIONES.
- MongoDB collection citas: 6 documents, same as Medilo.
- Supabase replica.citas: 6 rows before this run, matching Azure 6/6. The 18 SUPABASE deliveries stay PENDIENTE by design (Kafka consumer feeds Supabase); consumer run not verified in this session.
- Mongo fix: the Atlas role for replica_projection needed find/insert/remove/update on Medilo including collection `tombstones` (typo `tombstone` was corrected by the user).

## Pending (resume from here, in this order)
P1. Scheduling (Windows Task Scheduler, every 15 min): sync_external_writes and outbox_dispatcher. NEEDS USER DECISION: schedule from feature/replica-watermark-sync (requires the worktree kept) or after merging the PR to develop. The scheduled run needs the .env loaded (wrapper script) and KAFKA_BOOTSTRAP_SERVERS.
P2. Phase 2 (S4): detect hard deletes. Compare cita/usuario ids in Supabase and Mongo against Azure, queue deletes through the outbox (cita.eliminada / usuario.eliminado), bounded per cycle. Start with citas.
P3. T2b usuarios: reader with roles and paciente payload, same run_cycle pattern as citas.
P4. Consumer check: verify replica.citas after a Kafka consumer run (optional, small).
P5. Integration tests for T1, T2 and T3 against a test DB (not the production .env).
P6. Open PRs for the branches (user decides merge; ordinary repository policy).

## Log
L1. User request: implement the 15-minute sync with the watermark option, state in Azure (student tier), then complete pending work. Full-history sync requested and verified.
L2. Decision: rowversion watermark (usuarios.version_fila, citas.version_fila), state in Azure table. Hard deletes deferred to P2.
L3. Incident: scripts ran against local DB MedicalBooking when the env was not loaded; the watermark and outbox rows from that run exist in the LOCAL database only (4 cita events pending on KAFKA/MONGODB locally). Do not run the dispatcher with production replica settings against the local DB.
L4. Incident: Mongo ping passed while auth-requiring operations failed (ping needs no auth). Real checks use find on a collection.
L5. Other branches in flight: feature/replica-reconciliation (PR #44 draft), fix/seed-odbc-database (PR #45), feature/replica-watermark-sync (no PR yet).
