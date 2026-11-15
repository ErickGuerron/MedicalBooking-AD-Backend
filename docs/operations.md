# Operations runbook

Every command below is run from the repository root with PowerShell or a POSIX shell. Secrets
are read from `.env` (never committed) or from environment variables. No command prints a
secret.

## Setup

```powershell
uv sync                         # install locked dependencies (Python 3.12)
Copy-Item .env.example .env     # then fill in the values for your machine
```

Configuration keys are listed in `README.md` (section Configuration). The keys used by the
importers are `LOINC_USERNAME`, `LOINC_PASSWORD`, `ICD11_CLIENT_ID` and `ICD11_CLIENT_SECRET`.

## Database migrations

```powershell
uv run alembic current          # where the database is
uv run alembic upgrade head     # apply every pending migration
```

Migrations are never created with `create_all()`. The run is idempotent: the seeds use
`MERGE` or `IF NOT EXISTS`.

## Run the API

Local development:

```powershell
uv run flask --app medicalbooking.app:create_app run --port 5000
```

Container (production-like, one replica):

```powershell
docker build -t medicalbooking:local .
docker run -d --name api -p 8000:8000 --env-file .env `
  -v medicalbooking-documents:/app/data/documents medicalbooking:local
curl http://127.0.0.1:8000/health/live
```

The volume keeps uploaded PDFs across container restarts. The container user cannot write to
`/app`; it writes only to `/app/data/documents` (see `bcdc52b`). The SQL Server host in `.env`
must be reachable from inside the container.

Health probes: `GET /health/live` does not touch the database; `GET /health/ready` does.

## Tests and quality

```powershell
uv run pytest                        # unit and integration (integration needs SQL Server)
uv run pytest -m "not integration"   # unit only, no database
uv run ruff check .
uv run ruff format --check .
```

Integration tests use `MedicalBooking_test`, which is recreated at the start of the session.
The development database is never touched by the test suite.

## Workers

### Hold expiration (E4)

Releases pending appointments whose hold has ended. Safe to run several times and from several
processes at once.

```powershell
uv run python -m medicalbooking.workers.expire_holds   # prints expired_holds=N, exit 0 on success
```

Schedule it every few minutes (cron, Windows Task Scheduler, or an Azure job).

### Daily patient notifications (reminders and result mails)

These three run once a day, in this order, on the server that runs the API (the VMs in `deploy/`).
Each is a short job that exits when done; nothing here runs as a service.

1. `uv run python -m medicalbooking.workers.appointment_reminders` queues the reminder of every
   confirmed appointment of the day (Ecuador time). One reminder per appointment, however often it runs.
2. `uv run python -m medicalbooking.workers.outbox_dispatcher` turns the outbox events into
   notifications. Note: it also delivers to any other destination that is configured (Kafka,
   MongoDB, Supabase); those deliveries stay pending when they are not configured.
3. `uv run python -m medicalbooking.workers.send_notifications` sends the due mails through Brevo.
   Needs `BREVO_API_KEY` and `MAIL_FROM`.

Schedule them **every day at 06:00 (America/Guayaquil)**, in that order, stopping the run if a step
fails. Use cron on the VM, or an Azure scheduled job. Example for a Linux VM (cron runs in UTC, so
11:00 UTC is 06:00 in Ecuador, which has no daylight saving time):

```bash
0 11 * * * cd /opt/medicalbooking && uv run python -m medicalbooking.workers.appointment_reminders && uv run python -m medicalbooking.workers.outbox_dispatcher && uv run python -m medicalbooking.workers.send_notifications
```

Check it after the first run: each step prints only counts (`reminders_queued=N`,
`notifications attempted=N sent=N failed=N`) and exits with 0 on success.

### Knowledge importers (E5)

Imports terminology and evidence as drafts. The procedure is described in `knowledge-import.md`.

```powershell
uv run python -m medicalbooking.workers.import_knowledge --dry-run   # fetch and report, write nothing
uv run python -m medicalbooking.workers.import_knowledge             # fetch, then write in one transaction
```

Exit code 0 means the run finished. Exit code 1 means it did not, and the database is unchanged.

## Health of the knowledge base (quick checks)

```sql
SELECT estado, COUNT(*) FROM conocimiento_contenidos GROUP BY estado;     -- drafts vs published
SELECT nombre, codigo_loinc FROM conocimiento_analitos WHERE codigo_loinc IS NOT NULL;
SELECT codigo, estado FROM conocimiento_reglas;                           -- rules and their state
```

## Common failures

| Symptom | Cause | What to do |
|---|---|---|
| Container exits, workers fail with `PermissionError` | Storage path not writable by the application user | Use the image as built (`/app/data/documents`), or mount a writable volume there |
| `import not started: missing credentials` | A key is empty in `.env` | Fill the key; the message names it, never its value |
| `import aborted before any write: LOINC code_mismatch` | A code no longer matches the expected name | Review the code in `knowledge/sources.json`; nothing was written |
| `import rolled back, nothing kept: ImportFailed` | A rule named in the plan does not exist | Fix the rule code; nothing was written |
| `ICD-11: http_401` | Wrong client id or secret | Regenerate the access key in the ICD API portal |

### Candidate report and reviewed ranges (E5)

```powershell
uv run python -m medicalbooking.workers.knowledge_candidates          # writes knowledge/candidates/*.json, no database write
uv run python -m medicalbooking.workers.import_references --dry-run   # validate knowledge/references.csv and check its PMIDs
uv run python -m medicalbooking.workers.import_references             # write the drafts, all or nothing
```

See `knowledge-import.md` for the file format, the checks and the reviewer's steps.
