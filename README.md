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
| `FLUTTER_FRONT_URL` | `http://localhost:8081` | Base of the checkout return pages `/pago/exito` and `/pago/cancelado` |
| `LAB_DOCUMENTS_DIR` | `storage/documents` | Private folder for uploaded lab PDFs. Single host only until shared storage exists (E7). Never versioned |
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

## Endpoints

All endpoints except login and health require `Authorization: Bearer <token>`.
Errors use `application/problem+json` with `correlation_id`.

| Method and path | Purpose | Notes |
|---|---|---|
| `POST /api/v1/auth/login` | Sign in, returns a bearer token | Same answer for unknown email, wrong password and locked account. Five consecutive failures lock the account for 15 minutes; a success clears the counter |
| `GET /api/v1/me` | Current user, roles and permissions | |
| `POST /api/v1/users` | Create a user | Requires `usuarios.administrar` |
| `POST /api/v1/auth/register` | Patient self-registration, always `202` with the same message | Creates an inactive patient and sends an activation link by Brevo (`BREVO_API_KEY`, `MAIL_FROM`, `MAIL_FROM_NAME`). Same answer and work for registered and new emails. Rate-limited with login at Nginx |
| `GET /api/v1/auth/verify-email?token=...` | Activate the account from the email link | Single-use token valid 24 hours, stored only as a SHA-256 hash. Invalid, used or expired tokens get one generic `422` |
| `GET /api/v1/availability?desde=&hasta=&medico_id=` | Free slots in a window | ISO-8601 with offset; window at most 31 days; past slots never returned |
| `POST /api/v1/appointments` | Reserve a slot (status `PENDIENTE_PAGO`) | Requires header `Idempotency-Key` (8 to 100 characters). Same key and body returns the original (200); same key with another body returns 409; a new reservation returns 201 |
| `POST /api/v1/appointments/{id}/cancel` | Cancel an own appointment and release its slot | Missing and foreign appointments both return 403 |
| `POST /api/v1/invoices` | Invoice an own active appointment (one detail line at the service price) | Body `{"cita_id"}`. Status `PENDIENTE`; one invoice per appointment (409). Missing and foreign appointments both return 403 |
| `POST /api/v1/payments/checkout` | Start a Stripe Checkout for an own pending invoice | Body `{"factura_id"}`, header `Idempotency-Key`. 201 new, 200 replay of the same key and invoice, 409 for another invoice. Returns `checkout_url`. Payment is confirmed only by the verified webhook, never by the redirect. 503 when Stripe is not configured or unreachable |
| `POST /api/v1/payments/webhook` | Stripe event (no JWT) | Signature over the raw body; duplicates are recorded once |
| `POST /api/v1/lab/orders` | Order lab tests for a patient (status `SOLICITADA`) | Requires `ordenes.crear` and a doctor profile. Test types must be active |
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
| `POST /api/v1/knowledge/references/{id}/validate` | Validate a draft range | Requires `conocimiento.validar`. 409 when it overlaps a validated range |
| `POST /api/v1/knowledge/rules` | Create a declarative rule draft | Requires `conocimiento.administrar`. Numeric criteria need a unit |
| `POST /api/v1/knowledge/rules/{id}/validate` | Validate a rule draft | Requires `conocimiento.validar`. Only validated rules run |
| `POST /api/v1/lab/orders/{id}/evaluate` | Create the preliminary proposal of a complete order | Deterministic. One proposal per order. Never a diagnosis |
| `GET /api/v1/lab/orders/{id}/proposal` | Read the proposal | Ordering doctor only; denials are audited |
| `POST /api/v1/clinical/proposals/{id}/reviews` | Record the single medical decision | `ACEPTAR`, `CORREGIR` (needs the corrected conclusion) or `RECHAZAR` (needs a reason). The proposal content is never rewritten |

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
