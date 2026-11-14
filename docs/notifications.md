# Patient notifications

Patient-facing emails that tell a patient something changed. They are created from outbox
events and sent by a worker. Emails never contain clinical values, analyte names, diagnoses
or results: the patient reads the details inside the authenticated portal.

## Flow

1. A business change writes an outbox event in the same transaction (see `eventos_outbox`).
2. The outbox dispatcher hands each event to `NotificationOutboxHandler` (destination
   `NOTIFICACIONES`, seeded by migration `0022_notifications_destination`).
3. The handler inserts one `notificaciones` row (`PENDIENTE`). The idempotency key is
   `<event id>:<template>`, so delivering the same event twice creates one row.
4. The worker `medicalbooking.workers.send_notifications` claims due rows and sends them through
   Brevo.

## Events and templates

| Outbox event | Aggregate | Template |
|--------------|-----------|----------|
| `cita.reservada` | `cita` | `CITA_RESERVADA` |
| `cita.cancelada` | `cita` | `CITA_CANCELADA` |
| `resultado_laboratorio.registrado` | `orden_laboratorio` | `RESULTADO_LABORATORIO` |

Every other event is ignored (no row, success). Template text is fixed in
`modules/notifications/domain/templates.py`; it never includes event payloads.

## Delivery and retries

- Each claimed row gets one attempt per run. The claim leases the row for 5 minutes, so two
  workers never send the same row; a crashed worker's rows become due again after the lease.
- A failed attempt waits 1, 2, 4 and then 8 minutes before the next attempt.
- After 5 failed attempts the row is `FALLIDA`. The `numero_intentos` column counts attempts.
- Only `ultimo_error_codigo` is stored on failure: a short code such as `BREVO_HTTP_503`,
  `BREVO_UNREACHABLE` or an exception type name. Message bodies, tokens and the api key are
  never stored or logged.

## Running the worker

Run it on a schedule, for example every minute:

```bash
uv run python -m medicalbooking.workers.send_notifications
```

Required settings: `BREVO_API_KEY` and `MAIL_FROM`. Optional: `MAIL_FROM_NAME`.

Output: `notifications attempted=<n> sent=<n> failed=<n>`. Exit code `0` when the run finishes,
`1` when it fails or the configuration is incomplete. Details are never printed.

## Tests

- Unit: `tests/unit/test_notifications_*.py` (fake store and fake sender; Brevo through a mock
  transport, never the network).
- Integration: `tests/integration/test_notifications_*.py` against SQL Server (idempotent insert,
  leased claims, concurrent claims, state transitions).
