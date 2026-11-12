# SOAP contract, notifications and outbox dispatcher (E4 notifications, E6 SOAP, E8 prerequisite)
Objective: close spec section 11 items E4 (notifications), E6 (SOAP) and the outbox worker that E8 needs.
Branch: feature/soap-notifications-outbox (from develop cd767df). Delivery: ask-on-risk. Test runner: uv run pytest (set MEDICALBOOKING_TEST_DATABASE per worktree).

## Specs
S1. Endpoint `/soap/laboratory?wsdl` serves the WSDL. It exposes the operation `GetLaboratoryResultStatus(orderId)` (quoted from spec section 6). Authentication: a valid JWT bearer in the HTTP `Authorization` header. Authorization: the same rule as `GET /api/v1/lab/orders/{id}`. Missing and foreign orders answer the same SOAP Fault, so identifiers do not leak. Response: minimal status only (order id, status, timestamp). It never returns analyte values, ranges, proposals or diagnoses.
S2. An independent consumer test (tests/contract) calls the WSDL and the operation over HTTP without the server's own code, and checks a 401 without a token and an authorized success.
S3. Spec section 7.3: "Evento outbox inicia notificación". Events that tell a patient something changed (appointment booked or cancelled, lab order result ready) create `notificaciones` rows idempotently by `clave_idempotencia` (same event delivered twice creates one row). Emails never contain clinical values, analyte names, diagnoses or results.
S4. Spec section 7.3: "reintenta con backoff y evita correo con información médica sensible". Notification sends retry with exponential backoff, count attempts in `numero_intentos`, end FALLIDA after the maximum, and record only the error code (never the mail body, the token or the API key). Brevo is called through an adapter behind a port; tests use a fake sender.
S5. Outbox dispatcher: reads `eventos_outbox` in `version_agregado` order per aggregate, and creates one `entregas_eventos` row per active destination in `destinos_sincronizacion`. Entries for `SUPABASE` and `MONGODB` stay PENDIENTE: no handler is wired for them until E8. Re-running the dispatcher creates no duplicates (UQ `evento_id, destino_id`).
S6. Concurrency: several dispatcher or sender processes can run at once without double delivery (claim rows in SQL Server with a lease: `bloqueado_hasta`, UPDLOCK/READPAST). No in-memory locks.
S7. Every behavior has tests: unit tests for rules and integration tests against real SQL Server for claims, idempotency and retries. Each worktree uses its own `MEDICALBOOKING_TEST_DATABASE`.
S8. Documentation: help text of each new worker and a short section in docs/ for each. Do not change `docs/backend-status.md` in these units (the parent updates it at closure).

## Tasks
T1. SOAP contract (S1, S2). Route: delegated writer (unit A). Status: pending. Commit: -
T2. Notifications (S3, S4). Route: delegated writer (unit B). Status: pending. Commit: -
T3. Outbox dispatcher (S5, S6). Route: delegated writer (unit C). Status: pending. Commit: -
T4. Seam check: merge the three units, run the full suite once, RDD review if due, Risk line per commit. Route: inline (parent). Status: pending.

Route declaration: T1, T2 and T3 are parallel units with disjoint edit surfaces, so they are delegated writers under the Writer trigger (a).
Risk: T1 item 2 (new authenticated endpoint); T2 item 2 (sender credentials); T3 item 4 (concurrency).

## Log
L1 (user, verbatim): "ya realiza el contrato de SOAP sacando una rama de develol que esta al dia y dentro de esa rama usando worktrunk de github crea 3 workers paa implementar 1., 2. y 3. en paralelo e ir implementandolo delega als acciones a agentes competentes y siguiendo buenas practicas de programacion , haciendo uso de patrones apra optimizacioens si es necesario y al final revisa si todo lo indicado apra el proeyco final ya esta implementado dentro del backend para proseguir con la aprte de coneccion a mongo atlas y replicacion por emdio de kafka"
L2 (parent): 1. = SOAP contract, 2. = notifications, 3. = outbox dispatcher (from the gap analysis of the previous turn).
L3 (parent): decision, SOAP stack: hand-written WSDL and SOAP envelope handling, parsed with `defusedxml` (the only new dependency; stdlib XML parsers are unsafe on untrusted input). Spyne was rejected: heavy and unmaintained for Python 3.12. Revisit if the user prefers spyne.
L4 (parent): shared prerequisite done inline before the fan-out: `tests/conftest.py` reads `MEDICALBOOKING_TEST_DATABASE`, so parallel test sessions do not drop each other's database.
L5 (parent, open): Kafka replication conflicts with spec section 1 ("un broker de mensajería permanente" is out of scope). Not part of this feature; waiting for the user's decision.
