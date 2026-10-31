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
| `JWT_SIGNING_KEY` | none | Required outside `local`/`test` |

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
