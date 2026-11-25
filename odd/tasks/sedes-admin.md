# Sedes and consultorios administration (backend)

Objective: let an administrator create and manage sedes (branches) and their consultorios through the API. Branch: feat/sedes-admin (from develop). Delivery: ask-on-risk. Test runner: uv run pytest (integration needs SQL Server on localhost,1433).

## Specs

S1. An administrator can list every sede with its consultorios, including inactive ones: GET /api/v1/admin/sedes.
S2. An administrator can create a sede with nombre (required, non-blank, max 150) and direccion (optional, max 300). The zona horaria is not an input: every sede uses America/Guayaquil (single zone, decided by the user: "una sola zona").
S3. An administrator can edit a sede's nombre, direccion, and activo with PATCH /api/v1/admin/sedes/{id}.
S4. An administrator can create a consultorio in a sede with nombre (required, non-blank, max 80) with POST /api/v1/admin/sedes/{id}/consultorios. Duplicate nombre inside the same sede is refused with 409.
S5. An administrator can edit a consultorio's nombre and activo with PATCH /api/v1/admin/consultorios/{id}.
S6. Deactivating a sede, or a consultorio, is refused with 409 while any of its consultorios has an appointment in state PENDIENTE_PAGO or CONFIRMADA whose slot starts after now (UTC). The user decided: "Bloquear (Recomendado)": block, never cancel appointments.
S7. A consultorio cannot be created or reactivated in an inactive sede (400).
S8. These endpoints require the new permission sedes.administrar, granted to the ADMINISTRADOR role. Without it: 403. Without a valid session: 401.
S9. The permission is separate from horario.generar, which keeps working unchanged. The user decided: "Permiso nuevo (Recomendado)".
S10. GET /api/v1/admin/consultorios (used by the work-hours screen) keeps its current response: active consultorios only.
S11. The OpenAPI contract is updated for every new route before the code is considered done (AGENTS.md: contrato primero).

## Tasks

T1. Permission migration 0044 (sedes.administrar to ADMINISTRADOR) — S8, S9 — route: inline — status: done (SQL written; not run against SQL Server) — commit: slice 1
T2. Domain and ports: permission constant, records and store methods — S1-S7 — route: inline — status: done — commit: slice 1
T3. Service with rules (validation, conflicts, future-appointment guard) and unit tests with a fake store — S2-S7 — route: inline — status: done, 1666 unit tests pass — commit: slice 1
T4. SQL store implementation — S1-S7 — route: inline — status: done code, NOT verified against SQL Server — commit: slice 2
T5. Routes, wiring and OpenAPI fragments, docs snapshot — S1-S5, S8, S10, S11 — route: inline — status: done; contract test updated and passing — commit: slice 2
T6. Integration tests against SQL Server (sede lifecycle, guard, permissions, S10 unchanged) — S6-S10 — route: inline — status: written and collected (6 tests), NOT run: SQL Server unreachable on localhost,1433 and reading Windows service state was denied — commit: slice 2

## Log

L1 (user, verbatim): "Haber claudio, primero que nada buenas noches, seugno necesito que estudiemos el peso de los cambios para crear y manejar sucursales asi como historial medico, ve que es mas viable y en funcion de lo que sea mas viable veamos que hacer"
L2 (user, verbatim): "una sola zona, revisa que mas se requiere, realiza mas preguntas y empecemos por ello"
L3 (user, verbatim): "Sí, backend primero"
L4 (decisions): deactivation blocks on future appointments (user); permission is new (user); single zone (user). Default chosen by the assistant: one permission code sedes.administrar for sedes and consultorios; no uniqueness on sede names (shortcut: duplicate names are allowed, upgrade with a unique index if the admin UI needs it).
L5 (evidence): SQL Server not reachable on localhost,1433 at session start; integration tests are pending until it runs.
L6 (evidence): ruff check clean on src, tests and the migration; ruff format clean; 1666 unit tests pass, including the OpenAPI contract test with the four new paths.
L7 (blocked): an attempt to read the local SQL Server service state was denied by the auto-mode classifier, so the database was not started. Pending: run `uv run pytest tests/integration/test_office_admin_api.py` once SQL Server is up, and check the migration 0044 on a real database.
L8 (decision): the count of future bookings uses inicio_utc > now (UTC) and states PENDIENTE_PAGO and CONFIRMADA, per the citas CHECK and the active-state filtered index.
