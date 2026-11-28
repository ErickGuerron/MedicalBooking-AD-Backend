# MedicalBooking backend

Modular monolith (hexagonal architecture) for a clinical laboratory platform.
Flask API over SQL Server (local development first; Azure SQL later).

## Requirements

- Python 3.12 (`uv` manages it through `.python-version`)
- Microsoft ODBC Driver 18 for SQL Server
- SQL Server reachable at `localhost,1433` (default instance) for local work

## Setup

```powershell
uv sync
```

## Configuration

Settings are read from environment variables (or a git-ignored `.env` file).
Defaults target the local development instance.

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `local` | `local`, `test`, `production` |
| `SQLSERVER_HOST` | `localhost` | |
| `SQLSERVER_PORT` | `1433` | |
| `SQLSERVER_DATABASE` | `MedicalBooking` | Tests use `MedicalBooking_test` |
| `SQLSERVER_USERNAME` | empty | Empty means Windows authentication |
| `SQLSERVER_PASSWORD` | empty | Never commit |
| `SQLSERVER_DRIVER` | `ODBC Driver 18 for SQL Server` | |
| `SQLSERVER_ENCRYPT` | `yes` | |
| `SQLSERVER_TRUST_SERVER_CERTIFICATE` | `yes` | Local only. Azure SQL: `no` |
| `JWT_SIGNING_KEY` | none | Required outside `local`/`test`; at least 32 characters |
| `JWT_ACCESS_TTL_MINUTES` | `15` | Access token lifetime (1 to 60) |
| `APPOINTMENT_HOLD_MINUTES` | `15` | How long a pending appointment holds its slot (1 to 120) |
| `STRIPE_SECRET_KEY` | none | Stripe secret key for Checkout Sessions. Test key locally, live key only in the production secret store. Without it checkout answers 503 and creates nothing |
| `STRIPE_WEBHOOK_SECRET` | none | Signing secret of the webhook endpoint. Without it the webhook answers 503 |
| `STRIPE_PUBLISHABLE_KEY` | none | Publishable key `pk_test_...` (public). Returned with every checkout so the browser can mount the embedded session. Checkout answers 503 without it |
| `FLUTTER_FRONT_URL` | `http://localhost:8081` | Base of the checkout return pages `/pago/exito` and `/pago/cancelado` |
| `LAB_DOCUMENTS_DIR` | `storage/documents` | Private folder for uploaded lab PDFs when `DOCUMENT_STORAGE=local`. Single host only. Never versioned |
| `DOCUMENT_STORAGE` | `local` | `mongo` stores lab PDFs in MongoDB GridFS so every VM reads the same files. Needs `MONGODB_DATABASE` and `MONGODB_DOCUMENTS_URI` |
| `MONGODB_DOCUMENTS_URI` | unset | Mongo user with `readWrite` on `fs.files` and `fs.chunks`. Falls back to `MONGODB_PROJECTION_URI` when unset |
| `API_DOCS_ENABLED` | unset | Unset: on outside `production`, off in `production`. Set `true` or `false` to override |

## Database migrations

Schema is managed with Alembic and reviewed T-SQL scripts in `migrations/sql/`.
`create_all()` is never used.

```powershell
uv run alembic upgrade head
```

## Test accounts

Migration `0007` creates one shared set of accounts so every developer and both frontends sign in the
same way.

| Email | Role | Password |
|---|---|---|
| `paciente1@test.com`, `paciente2@test.com` | PACIENTE | `Password1` |
| `laboratorista1@test.com` | LABORATORISTA | `Password1` |
| `medico1@test.com` | MEDICO (can read `paciente1`) | `Password123` |
| `medico2@test.com` | MEDICO | `Password1` |
| `administrador1@test.com` | ADMINISTRADOR | `Password123` |

The migration does nothing when `APP_ENV=production`: a known password must never exist in a real
environment. If a deployed environment ever ran with another `APP_ENV`, remove the accounts.

## Tests

```powershell
uv run pytest                      # unit + integration (needs SQL Server)
uv run pytest -m "not integration" # unit only
```

Integration tests create and migrate a separate `MedicalBooking_test` database.

## Quality

```powershell
uv run ruff check .
uv run ruff format --check .
```

## Run the API locally

```powershell
uv run flask --app medicalbooking.app:create_app run --port 5000
```

Health probes: `GET /health/live` (no dependencies), `GET /health/ready` (database).

## Documentation

- `docs/backend-status.md`: delivery by phase, what is blocked and by whom, decisions, open advisories.
- `docs/operations.md`: commands for setup, migrations, running the API and the container, tests,
  workers and common failures.
- `docs/knowledge-import.md`: the external sources (LOINC, MedlinePlus, PubMed, ICD-11), how to obtain
  their credentials, how to run the importers, and their guarantees.
- `docs/clinical-knowledge.md`: how results are classified, how rules and proposals work, and the
  medical decision.
- `docs/deployment.md`: topology of four replicas on two VMs, deployment procedure, and what is still
  pending.
- `docs/api/openapi.json`: the OpenAPI document of the running API.
- `odd/tasks/`: one feature document per phase, with specs, tasks, decisions and evidence.

## External knowledge importers

Terminology and evidence come from public sources, imported as **drafts** with their provenance.
A doctor with `conocimiento.validar` publishes them. Published knowledge is never changed by an
import.

| Source | Responsibility | Credentials |
|---|---|---|
| LOINC | Confirms analyte codes (`codigo_loinc`); terminology only | `LOINC_USERNAME`, `LOINC_PASSWORD` |
| MedlinePlus Connect | Educational topics in Spanish for each LOINC code (drafts) | none |
| PubMed (E-utilities) | Evidence identity (PMID, title, journal) linked to rules | none |
| ICD-11 (WHO) | Hypothesis code for a rule, only when it is a draft | `ICD11_CLIENT_ID`, `ICD11_CLIENT_SECRET` |

```powershell
uv run python -m medicalbooking.workers.import_knowledge --dry-run   # fetch and report, write nothing
uv run python -m medicalbooking.workers.import_knowledge             # fetch, then write in one transaction
uv run python -m medicalbooking.workers.knowledge_candidates         # candidate report for review (no database write)
uv run python -m medicalbooking.workers.import_references --dry-run  # validate knowledge/references.csv
uv run python -m medicalbooking.workers.import_references            # write reviewed ranges as drafts
```

The APIs do not provide reference ranges. Ranges are read from studies by a person and recorded in
`knowledge/references.csv` with their PMID; see `docs/knowledge-import.md`.

The configuration is `knowledge/sources.json`. PubMed PMIDs are empty on purpose: choosing the
literature that supports a rule is a clinical decision. Step 1 fetches every source before any
write, so one failing source writes nothing. Step 2 writes in a single transaction: an error
halfway rolls back every earlier write. Re-running is safe; existing codes are never overwritten.

## Hold expiration worker

Pending appointments release their slot when the hold ends (`APPOINTMENT_HOLD_MINUTES`).
A separate one-shot process does this; the API replicas never run it:

```powershell
uv run python -m medicalbooking.workers.expire_holds   # prints expired_holds=N, exit 0 on success
```

Schedule it every few minutes (cron, Windows Task Scheduler, or an Azure job). Running it
from several processes at once is safe: each hold is claimed with row locks.

## Checkout retry worker

When Stripe cannot create a Checkout Session, the payment attempt stays pending and the
retry is scheduled: 1, 1, 2, 2 and 5 minutes, five retries at most. A separate one-shot
process runs the due retries; the API replicas never run it:

```powershell
uv run python -m medicalbooking.workers.retry_failed_checkouts   # prints checkout_retries attempted=N succeeded=M failed=K, exit 0 on success
```

Schedule it every minute. It only creates the Stripe session again (never a charge). Each
retry uses its own Idempotency-Key (`<attempt key>:<retry number>`), so overlapping runs
converge on the same session. An attempt whose invoice is no longer pending is cancelled.
After the fifth failed retry the attempt is rejected and the patient can start a new one.

The patient sees the state in the app with `GET /api/v1/invoices/{factura_id}/checkout-status`.
"Reintentar ahora" runs the next retry at once: send `reintentar_ahora: true` on
`POST /api/v1/payments/checkout`. Without that flag a routine reopen never spends a retry.
Patients can use it at most once a minute: a request sooner than one minute after the last
failure answers 409 and calls no provider.

## Endpoints

All endpoints except login and health require `Authorization: Bearer <token>`.
Errors use `application/problem+json` with `correlation_id`.

| Method and path | Purpose | Notes |
|---|---|---|
| `POST /api/v1/auth/login` | Sign in, returns a bearer token | Same answer for unknown email, wrong password and locked account. Five consecutive failures lock the account for 15 minutes; a success clears the counter |
| `GET /api/v1/me` | Current user, roles and permissions | |
| `POST /api/v1/users` | Create a user | Requires `usuarios.administrar` |
| `GET /api/v1/users?limit=&offset=` | List user accounts (no credentials) | Requires `usuarios.administrar`. Ordered by email; `limit` 1 to 200 (default 50), `offset` 0 or more; invalid values get `422` |
| `POST /api/v1/auth/register` | Patient self-registration, always `202` with the same message | Creates an inactive patient and sends an activation link by Brevo (`BREVO_API_KEY`, `MAIL_FROM`, `MAIL_FROM_NAME`). Optional `sexo` (`M` or `F`) and `fecha_nacimiento` (`YYYY-MM-DD`, not in the future, age at most 120) are stored on the patient record; invalid values get `422`. Same answer and work for registered and new emails. Rate-limited with login at Nginx |
| `GET /api/v1/auth/verify-email?token=...` | Activate the account from the email link | Single-use token valid 24 hours, stored only as a SHA-256 hash. Invalid, used or expired tokens get one generic `422` |
| `GET` / `PUT /api/v1/me/perfil-clinico` | Own altitude, smoking, sex and birth date | Patient only (`pacientes.editar_propio`), audited. `GET` returns all five fields, `null` when unknown. `PUT` needs altitude and smoking; `sexo` and `fecha_nacimiento` are optional: omitted leaves the stored value, `null` clears it, a value replaces it (same rules as registration). Unknown fields get `422` |
| `GET /api/v1/availability?desde=&hasta=&medico_id=&especialidad_id=` | Free slots in a window | ISO-8601 with offset; window at most 31 days; past slots never returned. Each slot carries `medico_id` and `medico` (`{id, nombre}`); `especialidad_id` (optional) keeps only slots of doctors with that specialty. Booking does not check the specialty (open decision) |
| `GET /api/v1/specialties` | Active specialties `{items:[{id, nombre, descripcion}]}` | Any signed-in user |
| `GET /api/v1/doctors?especialidad_id=` | Active doctors `{items:[{id, nombre, especialidades, duracion_cita_min, horario_atencion}]}` | Any signed-in user. No professional registry and no e-mail. `horario_atencion` is derived, not stored: `vigente_desde`/`vigente_hasta` are the earliest start and latest end of the doctor's active agendas (`hasta` null when an agenda is open-ended); `dias` has one `{dia_semana (ISO, 1 = Monday), desde, hasta}` per weekday that has slots in the next 31 days (not blocked), from the earliest start to the latest end in the site's local time. Gaps inside a day are not shown |
| `GET /api/v1/services` | Active services `{items:[{id, nombre, duracion_min, precio_base, moneda, especialidad_id}]}` | Any signed-in user. `precio_base` is a decimal string |
| `POST /api/v1/specialties`, `PATCH /api/v1/specialties/{id}` | Create a specialty; rename, describe or deactivate it | Requires `catalogo.administrar` (ADMINISTRADOR, migration `0030`). Duplicate name 409; unknown id 404. Audited in `auditoria_accesos` |
| `PUT /api/v1/doctors/{id}/specialties` | Replace a doctor's specialties `{especialidad_ids:[int]}` | Requires `catalogo.administrar`. Every id must be an active specialty (422); unknown doctor 404 |
| `POST /api/v1/services`, `PATCH /api/v1/services/{id}` | Create a service; set or clear (`null`) its `especialidad_id` | Requires `catalogo.administrar`. The service is created active; unknown or inactive specialty 422 |
| `POST /api/v1/appointments` | Reserve a slot (status `PENDIENTE_PAGO`) | Requires header `Idempotency-Key` (8 to 100 characters). Same key and body returns the original (200); same key with another body returns 409; a new reservation returns 201 |
| `GET /api/v1/appointments?estado=&limit=&offset=` | List own appointments, or the doctor's agenda | Patients see their own; each row carries its doctor as `medico` (`{id, nombre, registro_profesional, telefono}`) so the patient can contact them. A doctor with a doctor profile sees the agenda; each row carries `paciente` (`{id, nombre}`, the patient profile id lab orders use) and never the patient's contact data. Anyone else, administrators included, gets 403. Same paging rules as users |
| `GET /api/v1/appointments/cancellation-approvals?estado=&medico_registro=&limit=&offset=` | Cancellation review for administrators | Requires `citas.revisar_cancelaciones`. Rows carry `id`, `estado`, `inicio`, `fin`, `tiene_aprobacion` and the doctor's identity (`medico`: `{id, nombre, registro_profesional}`). Never a patient and never a doctor's phone. `medico_registro` is an exact match; blank means no filter. Every read and denial is audited (`listar_revision_cancelacion`) |
| `POST /api/v1/appointments/{id}/consultation` | The doctor of the agenda opens the consultation of one own appointment | Requires `consultas.abrir` (MEDICO, migration `0031`). One consultation per appointment, idempotent: `201` when created, `200` with the existing one on a repeat. The appointment must be `CONFIRMADA` or `COMPLETADA` (`409`); missing and foreign appointments both return `403`. Audited |
| `POST /api/v1/appointments/{id}/cancel` | Cancel an own appointment and release its slot; a doctor cancels an appointment of an own agenda | Patients send no body (any reason fields are ignored). A doctor must send `{"motivo_codigo": "IMPREVISTO_MEDICO" or "OTRO_MEDICO", "motivo_texto": 1 to 500 characters}`; otherwise 422 and nothing changes. Missing and foreign appointments both return 403. A paid cancellation requests the patient's refund |
| `POST /api/v1/invoices` | Invoice an own active appointment (one detail line at the service price) | Body `{"cita_id"}`. Status `PENDIENTE`; one invoice per appointment (409). Missing and foreign appointments both return 403 |
| `POST /api/v1/payments/checkout` | Start an embedded Stripe Checkout for an own pending invoice | Body `{"factura_id"}` plus optional `"reintentar_ahora"` (default false), header `Idempotency-Key`. With `reintentar_ahora` true a scheduled retry runs at once and uses one of its five retries; without it a pending attempt with a scheduled retry answers 409 and Stripe is not called. 201 new, 200 replay of the same key and invoice, 409 for another invoice. A new key on an invoice with a pending attempt first asks Stripe about that session (read only): open returns its checkout (200); paid reconciles the payment and the invoice to PAGADA and answers 409 without charging again; expired cancels the attempt and opens a new one (201). Stripe unreachable or an unexpected state answers 503 and changes nothing. Returns an embedded Checkout Session: `client_secret` (mounts the session in the browser) and `publishable_key`. The session belongs to the caller's Stripe customer (created on first use) and offers to save the card (`payment_intent_data[setup_future_usage]=off_session`). The session has no hosted URL. Its `return_url` is `FLUTTER_FRONT_URL/pago/retorno?session_id=...`. Payment is confirmed only by the verified webhook, never by the redirect. A confirmed payment (webhook, or the checkout reconciliation when Stripe already reports it paid) also moves the invoice's appointment from `PENDIENTE_PAGO` to `CONFIRMADA` in the same transaction (history row and replica event included); an appointment already cancelled or expired is left as it is and the payment is still confirmed. 503 when Stripe is not configured or unreachable |
| `POST /api/v1/payments/webhook` | Stripe event (no JWT) | Signature over the raw body; duplicates are recorded once |
| `GET /api/v1/invoices/{factura_id}/checkout-status` | Latest checkout attempt of an own invoice | Returns `pago_id`, `estado`, `reintentos_realizados`, `reintentos_maximos` (5), `reintentos_agotados`, `ultimo_error_codigo` and `proximo_reintento_en`. Foreign and missing invoices answer the same 403; an invoice without attempts answers 404. Read only: it never asks Stripe. |
| `POST /api/v1/invoices/{factura_id}/checkout-status/reconcile` | Settle the latest attempt against Stripe (own invoice) | Asks Stripe about the attempt: a paid session confirms the payment (as the webhook would), an expired one is cancelled, anything else changes nothing. It never opens a session or charges again. Answers the same body as the GET. |
| `GET /api/v1/me/payment-methods` | List the caller's saved cards, one page at a time | Requires `metodos_pago.gestionar` (patients). Read from Stripe on each call; items carry `id`, `brand`, `last4`, `exp_month`, `exp_year`, `is_default`. Query `limit` (1 to 100, default 10) and `starting_after` (a payment method id); anything else answers 422 before Stripe is called. Response `{items, has_more, next_cursor}`: `next_cursor` is the last item's id when `has_more` is true, otherwise null. No card data is stored, only the Stripe customer id in `stripe_clientes`. A user without a customer gets `{"items": []}` with no Stripe call |
| `POST /api/v1/me/payment-methods/setup-intents` | Start saving a card without paying | Requires header `Idempotency-Key` (8 to 100 characters; missing or invalid answers 422 with no provider call or local write). Creates the caller's Stripe customer on first use (Idempotency-Key `customer-<usuario_id>`), then an off-session SetupIntent with Idempotency-Key `setup-<usuario_id>-<client key>`, so a repeat returns the same SetupIntent. Returns `client_secret` and `publishable_key` (201). 503 when Stripe is not configured or unreachable; nothing is stored |
| `DELETE /api/v1/me/payment-methods/{payment_method_id}` | Remove one of the caller's saved cards | The card is read from Stripe first. Missing and foreign cards both answer 403 with the same body. 204 on success |
| `POST /api/v1/lab/orders` | Order lab tests for a patient (status `SOLICITADA`) | Requires `ordenes.crear` and a doctor profile. Test types must be active |
| `GET /api/v1/lab/orders?estado=&limit=&offset=` | List the orders the caller may read, newest first | Patient sees own orders; ordering doctor sees orders placed by them; others get 403. No result values in the list |
| `GET /api/v1/lab/orders/{id}` | Read an order | Only the patient or the ordering doctor; anything else is 403 |
| `POST /api/v1/lab/orders/{id}/results` | Enter results by hand for one test | Requires `resultados.registrar`. No clinical classification (E5). A test can be recorded once (409 otherwise) |
| `GET /api/v1/lab/orders/{id}/results` | Read the results | Patient or ordering doctor; each read is audited |
| `POST /api/v1/lab/orders/{id}/review` | Medical review with conclusion | Only the ordering doctor, once, after every test has results (409 otherwise). Results are never rewritten |
| `POST /api/v1/lab/orders/{id}/documents` | Upload the PDF report (`multipart`, field `archivo`) | Requires `resultados.registrar`. PDF only, `%PDF-` signature, at most 10 MB, stored privately |
| `GET /api/v1/documents/{id}/download` | Download a stored PDF | Patient or ordering doctor; the file hash is checked before sending |
| `GET /api/v1/knowledge/analytes` | List active analytes | Requires `conocimiento.leer`. Synthetic seed data only in this repository |
| `POST /api/v1/knowledge/analytes` | Create an analyte | Requires `conocimiento.administrar` |
| `GET /api/v1/knowledge/analytes/{id}/references?fecha=` | Validated reference ranges valid on a date | Drafts are never listed |
| `POST /api/v1/knowledge/analytes/{id}/references` | Create a draft reference range | Requires `conocimiento.administrar`. Invisible until validated |
| `GET /api/v1/knowledge/references?analito_id=&estado=` | List reference ranges with their state | Requires `conocimiento.validar` or `conocimiento.administrar`. Listing `BORRADOR` needs `analito_id`; unknown analyte gets 404 |
| `POST /api/v1/knowledge/references/{id}/validate` | Validate a draft range | Requires `conocimiento.validar`. 409 when it overlaps a validated range |
| `GET /api/v1/knowledge/rules?estado=` | List declarative rules | Requires `conocimiento.leer` (validated rules only). `conocimiento.validar` or `conocimiento.administrar` also see drafts; asking for `BORRADOR` without them gets 403 |
| `POST /api/v1/knowledge/rules` | Create a declarative rule draft | Requires `conocimiento.administrar`. Numeric criteria need a unit |
| `POST /api/v1/knowledge/rules/{id}/validate` | Validate a rule draft | Requires `conocimiento.validar`. Only validated rules run |
| `POST /api/v1/lab/orders/{id}/evaluate` | Create the preliminary proposal of a complete order | Deterministic. One proposal per order. Never a diagnosis |
| `GET /api/v1/lab/orders/{id}/proposal` | Read the proposal | Ordering doctor only; denials are audited |
| `POST /api/v1/clinical/proposals/{id}/reviews` | Record the single medical decision | `ACEPTAR`, `CORREGIR` (needs the corrected conclusion) or `RECHAZAR` (needs a reason). The proposal content is never rewritten |
| `GET /api/v1/lab/orders/{id}/sombra/snapshots/{snapshot_id}/hallazgos/{hallazgo}/ediciones` | Append-only edit history of one shadow finding | Same access as the shadow read (`resultados.leer_con_acceso` and the ordering doctor). Missing and foreign resources both return 403; every read is audited |

Booking is safe under concurrency: the slot is claimed with a conditional update and a
filtered unique index, so concurrent requests for one slot yield exactly one `201`.
Pending appointments release their slot when the hold ends (see the expiration worker below).

Rate limits are enforced by Nginx in front of the API (`deploy/nginx/`), per client address:
login accepts 5 attempts per minute, and the whole API accepts 20 requests per second (burst 40).
Rejected requests get `429` with `application/problem+json` and `Retry-After: 60`.
Each balancer keeps its own counters, so with two VMs the effective limit is per VM.

### API reference (Scalar)

With `API_DOCS_ENABLED` on (default outside production):

- Reference page: `GET /api/docs` (Scalar, can send requests with a bearer token).
- OpenAPI 3.1 document: `GET /api/v1/openapi.json`.

In production the routes return 404 unless `API_DOCS_ENABLED=true` is set explicitly.

Export the document without a running server or database:

```powershell
uv run python -c "from medicalbooking.app import create_app; from medicalbooking.config import Settings; c = create_app(Settings(_env_file=None, api_docs_enabled=True)).test_client(); open('docs/api/openapi.json','wb').write(c.get('/api/v1/openapi.json').data)"
```

`docs/api/openapi.json` is the committed copy used to generate clients (see `docs/`).
Regenerate it after any contract change.
