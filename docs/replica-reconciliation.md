# Replica reconciliation (phase 1)

The reconciliation worker finds outbox aggregates whose replica state disagrees with their last
event, and repairs them by requeuing that event's delivery. The outbox dispatcher then applies the
event again through its normal path. The worker never writes to MongoDB or Supabase itself.

Module: `medicalbooking.workers.reconcile_replicas`.

## What is checked

- **Aggregates in scope**: `usuario` (MongoDB `usuarios`, Supabase `replica.usuarios`) and `cita`
  (MongoDB `citas`, Supabase `replica.citas`).
- **Source**: for each aggregate, its latest event (highest `version_agregado`), when that event was
  created within the lookback window. The window defaults to 30 days and is set with `--days`.
- **Expected state**: the aggregate must be present in the replica, unless the latest event is a
  delete (`usuario.eliminado`, `cita.eliminada`), in which case it must be absent.
- **Destinations**:
  - MongoDB check: destination `MONGODB`, compared by `_id`.
  - Supabase check: destination `SUPABASE`, the dispatcher's direct handler for Supabase. Compared by
    `id` as lower-case text.
- **Mismatch**: a present-expected aggregate that is missing, or an absent-expected aggregate that
  still exists.

## Repair

For a mismatch, the latest event's delivery to the destination is requeued:

- If the delivery is `ENTREGADO`, it becomes `PENDIENTE` with `intentos=0`, and `proximo_intento`,
  `bloqueado_hasta`, `ultimo_error` and `entregado_en` are cleared.
- If no delivery row exists, one is inserted as `PENDIENTE`.
- Rows in `PENDIENTE`, `ERROR_TEMPORAL` or `ERROR_PERMANENTE` are never changed. Those belong to the
  dispatcher.

A repeat run does not requeue a delivery that is already `PENDIENTE`. The `repairs_*` counts are
requeues that changed a row, so a second run with nothing new reports zero.

## Hard deletes

A row deleted directly in SQL Server writes no outbox event, so the repair above cannot see it. The
worker detects these deletes by key reconciliation, once per cycle, before the repair pass:

- **Scope**: every `usuario` and `cita` id, not only those within the lookback window.
- **Read order**: the ids held by each configured replica are read first (MongoDB `_id`, Supabase
  `replica.usuarios` and `replica.citas`), then the source ids (`usuarios`, `citas`). A row created
  during the cycle reaches a replica only after it exists in the source, so it is never taken for a
  delete.
- **Orphan**: an id that any configured replica holds and the source does not.
- **Tombstone**: one `usuario.eliminado` or `cita.eliminada` event per orphan, written to
  `eventos_outbox` at the aggregate's latest version plus one, through the shared outbox helper. The
  dispatcher delivers it like any other event. An aggregate that already has a delete event gets no
  second one.
- **Skipped targets**: a replica whose settings are unset is not read, the same as in the repair
  pass.
- **Failures**: a failed replica or source read aborts the cycle before the first tombstone, so a
  partial view never creates a delete. Exit code `1`, error type only.

A tombstone written in a cycle can count as a repair in the same cycle. The repair pass then sees
the new delete event, and the replica still holds the row until the dispatcher applies the event.
The requeue is harmless, but the `repairs_*` count then includes that delete.

## Cadence and deployment

Run every 15 minutes, on one VM only. Two schedulers would not corrupt data, because the requeue is
idempotent, but they would duplicate the work and the logs.

Command, from the backend directory with the environment configured:

```text
uv run python -m medicalbooking.workers.reconcile_replicas
uv run python -m medicalbooking.workers.reconcile_replicas --days 30
```

Windows Task Scheduler, created in the UI or with `schtasks`, run as the user that owns the `.env`:

- Trigger: repeat every 15 minutes, indefinitely.
- Action: program `uv`, arguments `run python -m medicalbooking.workers.reconcile_replicas`.
- Start in: the backend directory (the one that holds `pyproject.toml`).

cron (Linux):

```text
*/15 * * * * cd /opt/medicalbooking-backend && uv run python -m medicalbooking.workers.reconcile_replicas
```

Use `--help` to print the module docstring.

## Configuration

| Setting | Effect when unset |
| --- | --- |
| `MONGODB_PROJECTION_URI` and `MONGODB_DATABASE` | MongoDB check skipped. A warning goes to stderr. |
| `SUPABASE_DATABASE_URL` | Supabase check skipped. The output shows `skipped_supabase=True`. |

Both connection strings are secrets and are never printed or logged.

## Required Supabase role privileges

`SUPABASE_DATABASE_URL` must connect as a role that sees every row of the two tables
it reads:

- `SELECT` on `replica.usuarios` and on `replica.citas`.
- If row-level security is enabled on those tables, the role must bypass it (`BYPASSRLS`) or own
  the tables. Otherwise rows are hidden, the reconciler sees them as missing, and it requeues
  deliveries that do not need it.

The role needs no write privilege. Keep it separate from the reader and writer roles.

## Output

```text
aggregates=<n> repairs_mongodb=<n> repairs_supabase=<n> skipped_supabase=<True|False>
```

Exit code `0` means the cycle finished. Exit code `1` means it could not start or failed. The worker
prints only counts and error types. The hard-delete counts (`orphans=<n> tombstones=<n>`) are logged at INFO
level, not printed on this line. The worker does not configure logging, so they show only when the
caller sets up logging. Connection strings, payloads and event ids are never printed.

## What it does not cover

- Aggregates with no outbox event in the replica. A replica row that has no source row is a hard
  delete and is tombstoned (see Hard deletes), but a source row that never reached the outbox is
  not replicated and is not detected.
- Aggregate types other than `usuario` and `cita`. Other types are phase 2.
- Events older than the lookback window. Their latest event is not checked.
- Repairs for projections the handler cannot apply. If an event type has no projection handler,
  the handler writes nothing, and the next cycle requeues the same delivery again. Today every
  `cita` write emits the `cita.actualizada` snapshot, which is projected, so this does not occur
  for the current event flow.

## Failure modes

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Exit 1, `replica reconciliation failed: ServerSelectionTimeoutError` | MongoDB unreachable | The checks run before any requeue, so this failure requeues nothing. Check the Atlas network access and the URI. |
| Exit 1, `replica reconciliation failed: <driver error type>` | Supabase role missing privileges, or Supabase unreachable | Check the grants and the connection in `SUPABASE_DATABASE_URL`. |
| Exit 1 after some repairs | SQL Server error during the requeue loop | Each requeue is its own transaction, so repairs already made stay made. The next run finishes the rest. |
| `repairs_*` non-zero every cycle | The dispatcher cannot apply the event, or the dispatcher is not running | Check the dispatcher schedule and the delivery error states. |
| `repairs_supabase=0` but the replica is stale | Supabase check skipped, or RLS hides rows from the role | Check `skipped_supabase` and the role privileges. |
| Exit 1 before any repair, hard-delete step | A replica or source read failed; no tombstone was written | Fix the connection or grants named in the error type; the next run retries the whole cycle. |
| Deliveries stay `PENDIENTE` for a long time | The dispatcher is behind, or an earlier event for the same aggregate is pending | The dispatcher claims events per aggregate in version order. |

## Ordering with the dispatcher

A requeued delivery is picked up by the next dispatcher cycle. The dispatcher still claims an event
only after the earlier events of the same aggregate are done. The reconciler never changes an
in-flight delivery, because an in-flight delivery is `PENDIENTE` or leased and not `ENTREGADO`.
