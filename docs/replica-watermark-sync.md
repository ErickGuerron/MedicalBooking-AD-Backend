# External-write sync (watermark)

> Status: describes the planned worker (T3). The store (T1) and the appointment reader (T2) are built;
> the worker and its CLI are not, so the commands below do not run yet.

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

Run on one machine only, the same as the reconciliation worker, so two schedulers do not duplicate the
delivery. Windows Task Scheduler: repeat every 15 minutes, program `uv`, arguments
`run python -m medicalbooking.workers.sync_external_writes`, start in the backend directory.

## Outage recovery

If Supabase or MongoDB is down, the outbox dispatcher keeps the events it could not deliver. Rows written
through the API in that window are sent when the dispatcher runs again. Rows written outside the API are
sent by the next watermark cycle after the replica comes back, because the watermark did not advance past
them.

## Configuration

| Setting | Effect when unset |
| --- | --- |
| `SUPABASE_DATABASE_URL` | Supabase delivery skipped; watermark does not advance for the skipped destination |
| `MONGODB_PROJECTION_URI` and `MONGODB_DATABASE` | MongoDB delivery skipped; watermark does not advance for the skipped destination |

Connection strings are secrets and are never printed or logged.
