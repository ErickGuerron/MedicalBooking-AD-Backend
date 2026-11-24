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

- T1 | S1 | inline | pending | RED: payload test for horario_trabajo.declarado (unit) | GREEN: emit in replace_work_blocks
- T2 | S2 | inline | pending | migration replica 0015 + projection handler + unit test
- T3 | S3, S5 | inline | pending | catalog snapshot of declared hours and franja
- T4 | S4 | inline | pending | contingency read endpoints (work hours, availability), read-only
- T5 | S6 | - | pending | review of commits by the user; no push, no Supabase apply until approved

## Log

- L1 (user, verbatim): "los horarios de trabajo no se les muestra a los medicos o pacientes porque recuerda que el paciente tiene derecho a consultar cuando puede tener una cita y el doctor a saber el horario asignado"
- L2 (user, verbatim): "diria que lo mejor seria aplicarlo tanto a nivel de A como B porque, si hago insercion o hay algun error se puede actualizar dentro de 15 minutos y en el outbox igual para que pueda ver replicacion instantanea si no falla la consulta"
- L3 (user): "si"
- L4 (parent): the outbox dispatcher runs every minute (cron VM1), so the event path is about one minute, not instantaneous. The snapshot repairs at most 15 minutes after a failure.
- L5 (parent): the earlier claim that the schedule was internal was wrong. The feature is merged in PR #107 and used by scheduling/infrastructure/repositories.py; the search that missed it was not recursive.
