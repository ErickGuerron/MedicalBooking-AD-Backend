# External-write sync (watermark)

> Status: the worker (T3) is built and unit-tested. The SQL integration path and the scheduled
> deployment are not verified yet.

Phase 1 reconciliation (`docs/replica-reconciliation.md`) repairs replicas from the outbox. It cannot
see writes that bypass the API, such as external SQL inserts, or writes made while Supabase or MongoDB
were down with no event recorded. This job covers those writes.

Module: `medicalbooking.modules.synchronization` (watermark store and changed-row reader).

## What it does

Every 15 minutes it reads, per source table, the rows whose row version is above the stored watermark,
and publishes them to the replicas through the same projection path the outbox uses.

- **Source versions**: `usuarios.version_fila` and `citas.version_fila` (SQL Server `rowversion`). The
  version moves forward on every insert and update, including writes made outside the API.
- **Watermark**: one row per table in `sincronizacion_watermarks` (migration 0028), stored in Azure SQL.
  It advances only after its batch is delivered, and it never moves backwards.
- **Snapshots**: a changed appointment is sent as the full `cita.actualizada` snapshot, so the replica
  applies it the same way it applies an API write.

## Limits

- **Hard deletes are not detected.** A deleted row has no version left to read. Deletes are a separate
  phase that compares identifiers across the databases.
- **Users are not in this slice yet.** A user projection needs its roles and patient profile, so
  `usuarios` is read in a later change.
- **Replica state is eventually consistent.** A change appears in Supabase and MongoDB within one cycle
  (about 15 minutes) plus the dispatcher's time.

## Deployment

Run on one machine only, so two schedulers do not queue the same rows twice. Windows Task Scheduler:
repeat every 15 minutes, program `uv`, arguments `run python -m medicalbooking.workers.sync_external_writes`,
start in the backend directory.

Each batch of up to 500 rows is queued together with its watermark in one SQL transaction. A rerun after
a crash therefore never skips rows, and it never queues a row twice for the same watermark.

## Outage recovery

If Supabase or MongoDB is down, the worker still queues external writes in the outbox, because it needs only
SQL Server. The dispatcher keeps retrying delivery until the replica returns. The watermark advances when
the event is queued, not when it reaches the replica: delivery after an outage is the outbox's job.

## Configuration

The worker needs only the SQL Server settings (`SQLSERVER_*` or `AZURE_DATABASE_URL`). It never
connects to Supabase or MongoDB: it queues outbox events, and the dispatcher delivers them. Connection
strings are secrets and are never printed or logged.

Output: `queued=<n>`. Exit code 0 means the cycle finished; 1 means it failed.
