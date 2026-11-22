# Admin reports and monitoring
Objective: read-only reports and monitoring screens for the administrator, backend plus Angular.
Branch: feature/admin-reports-monitoring (worktree of feature/cancellation-candidates at afb4d2e). Delivery: ask-on-risk. Tests: pytest (backend), vitest and ng build (Angular).

## Specs
S1. Reports: "prepara el modulo de reportes y monitoreo del adminsitrador". Summary per period (desde, hasta inclusive, max 366 days): appointments by estado (reservada_en), lab orders by estado (fecha_orden), payments by estado with count and amount (creado_en), treatments by estado (fecha_inicio).
S2. Monitoring: Azure SQL latency of a trivial query; per replication destination (codigo in destinos_sincronizacion): counts by estado of entregas_eventos, oldest pending event (creado_en), latest successful delivery (entregado_en).
S3. Access: reportes.leer for the summary, monitoreo.leer for monitoring, both granted to ADMINISTRADOR only. A missing permission answers the existing 403 ("Access to this resource is not allowed").
S4. Read-only: no writes, no outbox events. Invalid period (desde after hasta, or more than 366 days) answers a validation error.
S5. Angular: routes /reportes and /monitoreo, shown in the home modules only to holders of the permission. Spanish UI, English code.
S6. Out of scope: PDF export of the report, live pings to Supabase and MongoDB (the monitoring reads outbox state instead), and the experiments (VMs are off).
ASSUMPTION A1: the replicas are judged by their delivery backlog, not by a live connection check, to keep the endpoint free of extra network dependencies.
ASSUMPTION A2: the migration 0041 is parented on 0038_result_review. Migrations 0039 and 0040 are uncommitted in the main working tree; when they are committed, 0041 must be re-parented on 0040 (single head).

## Tasks
T1 (S3, S4): migration 0041 with permissions reportes.leer and monitoreo.leer for ADMINISTRADOR. Route: inline. Status: pending.
T2 (S1, S2, S4): module modules/reporting (domain policy, use cases, SQL store, routes, openapi, app wiring). Route: inline. Status: pending.
T3 (S1-S4): backend tests (unit policy and service, integration API). Route: inline. Status: pending.
T4 (S5): Angular domain, data, pages, routes, navigation entries. Route: inline. Status: pending.
T5 (S5): Angular tests, ng build. Route: inline. Status: pending.

## Log
L1 (user, verbatim): "ya ahora neceisot que revises proeycto_final_integrador para ver que funcionalidad falta implementar" ... "prepara el modulo de reportes y monitoreo del adminsitrador, y revisa si en flutter que vistas estan faltantes" ; "las pruebas de experimento no va a valer porque als maquians estan apagadas".
L2: experiments (T9) are excluded for this iteration by the user, VMs are off.
L3: Flutter review (missing views) is a read-only report, not implementation.
