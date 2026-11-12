# Outbox dispatcher

The dispatcher reads `eventos_outbox` and delivers each event to every active destination in
`destinos_sincronizacion`. It is the worker behind spec section 7.3 and the prerequisite for the
E8 synchronization to Supabase and MongoDB.

## Run

```bash
uv run python -m medicalbooking.workers.outbox_dispatcher
uv run python -m medicalbooking.workers.outbox_dispatcher --help
```

Run it on a schedule, for example every minute. Several processes may run at once.

Output is counts only: `materialized`, `delivered`, `retry_scheduled`, `permanent_failures` and
`lease_lost`. Exit code 0 means the cycle finished; 1 means it failed (only the error type is logged,
because database messages may carry connection data).

## How a cycle works

1. **Materialize.** One `entregas_eventos` row (PENDIENTE) per event and active destination. Re-running
   creates no duplicates (unique `evento_id, destino_id`).
2. **Claim.** Due rows (PENDIENTE or ERROR_TEMPORAL with `proximo_intento` reached) are leased in
   SQL Server with `UPDLOCK, READPAST`: `bloqueado_hasta` is set to now + 5 minutes. Concurrent
   dispatchers skip rows another one holds, so no row is leased twice.
3. **Order.** A delivery is claimable only when no earlier event of the same aggregate and destination
   is still PENDIENTE, ERROR_TEMPORAL or leased.
4. **Deliver.** The destination handler runs outside any database transaction.
5. **Complete.** The outcome is written only if the lease is still the one this dispatcher holds.

## Outcomes

| Handler result | Estado | Notes |
|----------------|--------|-------|
| Returns | ENTREGADO | `entregado_en` set, lease cleared |
| `TransientDeliveryError` or any other exception | ERROR_TEMPORAL | retry after the backoff below |
| `PermanentDeliveryError` | ERROR_PERMANENTE | no more retries |
| Tenth failed attempt | ERROR_PERMANENTE | `MAX_ATTEMPTS = 10` |

Backoff after failed attempt n: 1, 2, 4, 8, 16, 32 minutes, then 32 minutes. `intentos` counts every
handler call, successful or not. `ultimo_error` stores the exception class name only.

## Destinations without a handler

`NOTIFICACIONES` is wired to the notification handler. `SUPABASE` and `MONGODB` have rows materialized,
but no handler is registered until E8. Their rows stay PENDIENTE with `intentos = 0`.
`build_handlers` in `workers/outbox_dispatcher.py` is the only place to register a handler.

## Guarantees and limits

- **At least once.** A lease can expire while a handler is still running (for example after a crash).
  The event is then delivered again, so every handler must be idempotent.
- **Per-aggregate order** applies per destination. A destination that is activated later does not get
  rows for older events, and those events do not block it.
- **No in-memory locks.** Coordination is only through SQL Server leases.
