Objective: every 15 min, copy into Supabase and MongoDB the Azure SQL rows written outside the API (external SQL inserts, and writes made while Supabase or Mongo were down).
Branch: feature/replica-watermark-sync (from feature/replica-reconciliation 988d36f). Delivery: ask-on-risk. Test runner: uv run pytest tests/unit (never integration against the production .env).

## Specs
S1. Every 15 min, read usuarios and citas rows whose version_fila (CAST AS BIGINT) is greater than the stored watermark, and publish them to the replicas through the existing projection handlers (MONGODB and KAFKA destinations).
S2. The watermark is stored per table in Azure SQL in a small table (sincronizacion_watermarks: tabla, ultima_version BIGINT, actualizado_en). Advance it only after the batch is delivered.
S3. A first run with no watermark starts at 0 and covers the lookback window only if the user asks; otherwise it covers everything.
S4. Hard deletes are not visible to the watermark. Deletes are out of this slice (phase 2: ID comparison).
S5. Reuse the outbox delivery path: no direct writes to MongoDB or Supabase from the worker.
S6. The worker never prints connection strings, payloads, or row contents; it prints counts and error types only.
S7. Re-running the worker with no new rows changes nothing.

## Tasks
T1. Watermark store (table DDL + repository), unit tests. Route: inline. Status: done in code (unit 1 passed, ruff clean; integration not run locally). Integration test pending a test DB run.
T2. Changed-row reader for citas (CAST version_fila), unit tests (b43e4b3). Route: inline. Status: done for citas; usuarios pending (needs roles and paciente projection, see T2b). Integration test pending a test DB run.
T2b. Changed-row reader for usuarios (roles and patient profile in the payload). Status: pending.
T3. Worker: read changes, publish through outbox path, advance watermark after success, unit tests (S1, S2, S5, S7). Route: inline. Status: pending.
T4. CLI entry point and docs/replica-reconciliation.md section (S6). Route: inline. Status: pending.
T5. Migration for sincronizacion_watermarks, applied to Azure only after explicit user confirmation. Status: pending, blocked on user go-ahead.

## Log
L1. User request (verbatim, paraphrased by the user as): "ya quedamos con implementar la de 15 minutos mas flexible y la que mejor se adapta con mi tier de estudiante" and "me parece una buena opcion" (watermark state stored in Azure).
L2. Decision: rowversion watermark (usuarios.version_fila, citas.version_fila), state in Azure table. Hard deletes deferred to S4.
