Feature: replicate the doctors' declared work hours to the Supabase replica (outbox + snapshot).
Branch: feat/replica-work-hours (from develop). Delivery: ask-on-risk. Test runner: uv run pytest -m "not integration" (unit); integration needs SQL Server.

## Specs

- S1. Each change of a doctor's declared work hours emits an outbox event `horario_trabajo.declarado` in the same SQL Server transaction, carrying the whole block set of that week (`vigente_desde`), so a replay replaces it. Source: user's decision "aplicarlo tanto a nivel de A como B".
- S2. The Supabase replica stores the declared weeks in `replica.horarios_trabajo_version` (medico_id, vigente_desde, bloques, version), read-only for the reader role. A stale event (version not newer than the stored row) never overwrites a newer row.
- S3. The catalog sync copies the full declared-hours table to the replica every 15 minutes as a snapshot, in one transaction, so events that failed after retries are repaired. Same version guard as S2.
- S4. Patients and doctors may see when a doctor works (user: "el paciente tiene derecho a consultar cuando puede tener una cita y el doctor a saber el horario asignado"). Contingency read of work hours and availability is read-only; no writes during an Azure SQL outage.
- S5. The directory sync carries `servicios_medicos.franja` to the replica.
- S6. Nothing is pushed or applied to Supabase until the user reviews the commits (user: "hasta que lo revises").

## Tasks

- T1 | S1 | inline | done (d1a0cbb) | RED: test_horario_trabajo_payload ImportError; GREEN: declared_hours_payload + append_outbox in replace_work_blocks; unit suite 1650 passed
- T2 | S2 | inline | done (cedfc60) | RED: test_replica_work_hours_projection failed; GREEN: handler, upsert_horario_version with version guard, migration migrations_supabase/sql/0015_replica_work_hours.sql; unit suite 1652 passed
- T3a | S3 | inline | done (2589475) | RED: test_declared_hours_snapshot collection error; GREEN: declared_hours_sync (build + guarded upsert), declared_hours_source (SQL Server reader), wired into sync_replica_catalog; reader SQL verified against Azure with a read-only SELECT (0 weeks, data empty). Unit suite 1654 passed
- T3b | S5 | inline | pending | franja in directory snapshot. Changing the servicios tuple also touches test_directory_sync, test_mongo_directory and test_catalog_api; needs its own review
- T6 | S1-S3 (Atlas) | inline | done in code, commit pending review | RED: test_mongo_declared_hours collection error; GREEN: horario_key, _MongoTransaction.upsert_horario_version, mongo_declared_hours snapshot wired in sync_replica_catalog. Collection horarios_trabajo. Unit suite 1659 passed. Not verified: the Atlas projection role must have write access to this collection
- T4 | S4 | inline | pending | contingency read endpoints for work hours and availability (read-only, no writes); not started
- T5 | S6 | - | pending | user review of commits d1a0cbb, cedfc60, 2589475; no push, no migration 0015 apply to Supabase until approved

## Log

- L1 (user, verbatim): "los horarios de trabajo no se les muestra a los medicos o pacientes porque recuerda que el paciente tiene derecho a consultar cuando puede tener una cita y el doctor a saber el horario asignado"
- L2 (user, verbatim): "diria que lo mejor seria aplicarlo tanto a nivel de A como B porque, si hago insercion o hay algun error se puede actualizar dentro de 15 minutos y en el outbox igual para que pueda ver replicacion instantanea si no falla la consulta"
- L3 (user): "si"
- L4 (parent): the outbox dispatcher runs every minute (cron VM1), so the event path is about one minute, not instantaneous. The snapshot repairs at most 15 minutes after a failure.
- L6 (parent, check): the new event passes through every outbox destination. Mongo records an unknown event type as processed without applying it, and the notifications handler ignores types it does not template, so no destination blocks on it.
- L7 (parent, not verified): the integration tests against a real SQL Server (outbox row in the same transaction, snapshot end to end) have not been run in this session. Only unit tests and a read-only query against Azure were run.
- L8 (parent): nothing was written to Azure by this work. Supabase received nothing; migration 0015 is in the repo only.
- L5 (parent): the earlier claim that the schedule was internal was wrong. The feature is merged in PR #107 and used by scheduling/infrastructure/repositories.py; the search that missed it was not recursive.
