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
| `API_DOCS_ENABLED` | unset | Unset: on outside `production`, off in `production`. Set `true` or `false` to override |

## Database migrations

Schema is managed with Alembic and reviewed T-SQL scripts in `migrations/sql/`.
`create_all()` is never used.

```powershell
uv run alembic upgrade head
```

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

## Endpoints

All endpoints except login and health require `Authorization: Bearer <token>`.
Errors use `application/problem+json` with `correlation_id`.

| Method and path | Purpose | Notes |
|---|---|---|
| `POST /api/v1/auth/login` | Sign in, returns a bearer token | Same answer for unknown email, wrong password and locked account. Five consecutive failures lock the account for 15 minutes; a success clears the counter |
| `GET /api/v1/me` | Current user, roles and permissions | |
| `POST /api/v1/users` | Create a user | Requires `usuarios.administrar` |
| `GET /api/v1/availability?desde=&hasta=&medico_id=` | Free slots in a window | ISO-8601 with offset; window at most 31 days; past slots never returned |
| `POST /api/v1/appointments` | Reserve a slot (status `PENDIENTE_PAGO`) | Requires header `Idempotency-Key` (8 to 100 characters). Same key and body returns the original (200); same key with another body returns 409; a new reservation returns 201 |
| `POST /api/v1/appointments/{id}/cancel` | Cancel an own appointment and release its slot | Missing and foreign appointments both return 403 |

Booking is safe under concurrency: the slot is claimed with a conditional update and a
filtered unique index, so concurrent requests for one slot yield exactly one `201`.
Pending appointments do not expire yet; the expiration worker belongs to the payment phase.

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
