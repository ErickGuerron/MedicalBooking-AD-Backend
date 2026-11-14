Objective: REST endpoints for medical treatments and follow-up, defined by the treating doctor for a patient's appointment (integrator project, Medilo backend). Project: practica_azure (medicalbooking-backend).
Branch: feature/treatments (from develop 5de6ba6). Delivery: ask-on-risk. Test runner: uv run pytest tests/unit (integration only against a test DB, never the production .env). Migration 0029 is NOT applied to Medilo until the user explicitly authorizes it.

## Specs
S1. Source requirement (proyecto_final_integrador.md, section 9, item 4): "Servicio de tratamientos (REST/JSON): Tratamientos y seguimiento."
S2. Source requirement (section 7 flow, step 5): "El médico revisa y define tratamiento."
S3. Source requirement (rubric criterion 4): "Servicios REST funcionales para citas, pagos, resultados y tratamientos."
S4. A treatment belongs to one cita (appointment) and is created only by the doctor of that cita. Each treatment has a description, a state (ACTIVO, FINALIZADO, SUSPENDIDO), a start date, and an optional end date.
S5. Follow-up entries (seguimientos) are append-only notes linked to a treatment, written by the doctor of the cita. They are never edited or deleted through the API.
S6. The owning patient may read their own treatments and follow-ups. A missing resource and a foreign one both answer 403 (same convention as laboratory).
S7. Treatment data and follow-up notes are clinical notes: they are NOT replicated to Supabase or MongoDB (same privacy rule as consultas_medicas notes in kafka-replication.md). Add them to the never-replicated list.
S8. plan_tratamiento in consultas_medicas is untouched. It stays a free-text summary and is not read or written by the new endpoints.
S9. Assumption (to be confirmed by the user if wrong): the link is cita_id because no code path writes consultas_medicas today.
S10. Writes are audited without clinical values (same as laboratory).

## Tasks
T1. Migration 0029: tables tratamientos and tratamientos_seguimientos, constraints, indexes, and any permission seed the existing permisos model needs. Status: done (no permission seed: existing permissions reused; down_revision 0027_doctor_cancel_limits, see Log L5). Not applied to any database.
T2. Domain policies: state validation, text length limits, date rules. Status: done (treatments/domain/policies.py).
T3. Application service (TreatmentService) with store port, authorization, audit. Status: done (application/use_cases.py, ports.py).
T4. Infrastructure repository (T-SQL, parameterized). Status: done (integration verified against SQL Server, see L9).
T5. HTTP routes under /api/v1 with the authenticated decorator, pydantic request models (extra forbidden), JSON responses, OpenAPI entries. Status: done (presentation/routes.py, openapi.py).
T6. Register the blueprint in app.py and the composition root. Status: done (_build_treatments, treatments_bp, openapi fragment).
T7. Unit tests for policies, service, and routes (RED first for each rule). Status: done (test_treatments_*.py; see Log L6 for the RED record and the one exception).
T8. Never-replicated list update in docs/kafka-replication.md. Status: blocked (docs/kafka-replication.md does not exist; the list lives in odd/tasks/kafka-replication.md line 51, outside this unit's surfaces; see Log L7).
T9. Docs: endpoints in the API documentation. Status: done (docs/treatments.md).

## Log
L1. User request (verbatim): "termina de implementar los endpoints que son del tratamiento" (after confirming option A: entidad estructurada con seguimientos, plan_tratamiento as summary).
L2. Finding: no treatment endpoints or code existed in any worktree. plan_tratamiento is only a column in consultas_medicas, and no code writes consultas_medicas.
L3. Constraint: migration 0029 on Medilo (production) requires explicit user authorization. Creating and testing it locally is allowed.
L4. Discrepancy: the brief named down_revision 0028_sincronizacion_watermarks. That file does not exist on feature/treatments or develop. The real head is 0027_doctor_cancel_limits, so 0029 follows it (no gap). Parent to confirm.
L5. Permission decision: no seed. Writes reuse revisiones.registrar (S2: the doctor reviews and defines the treatment). Reads reuse resultados.leer_con_acceso (doctor) and resultados.leer_propios (patient). The names are laboratory-flavoured; the per-appointment check is the real gate. A dedicated tratamientos.* pair is the alternative if the parent prefers clean names.
L6. TDD record: RED for policies, service and routes was observed (module missing, then failing assertions for the follow-up order and the 403/422 rules). The migration and its test were written before the test (not a true RED for the migration; its tests are static). The contract RED was observed (3 failing OpenAPI tests) before the fragment existed.
L7. T8 blocked: docs/kafka-replication.md is absent. The never-replicated list is in odd/tasks/kafka-replication.md (line 51). Parent to decide: add the two tables to that file (outside this unit) or create docs/kafka-replication.md.
L8. Integration not run: the T-SQL adapter (repositories.py) and migration 0029 were not executed against SQL Server. They need the integration suite on a test database.
L9. Integration done: tests/integration/test_treatments_api.py (9 tests) runs against the MedicalBooking_test database through the real app and SqlTreatmentStore, with migration 0029 applied by the fixtures. Covers S1-S6 and S10 access/audit paths: create and list by doctor and owning patient; PATCH and FINALIZADO end-date rule; follow-up order; append-only trigger asserted via SQL; foreign doctor, foreign patient and missing ids all answer the same 403 body; 401 without token. Goal 7 (migration downgrade) skipped: no integration downgrade pattern in the repo; tests/unit/test_treatments_migration.py covers it statically. No implementation bug found. RED caveat: implementation pre-existed, so these were characterization tests that passed on first run after one test-side fix (correlation_id differs per response). Counts: integration 445 passed, 40 skipped, 1 xfailed before; 454 passed, 40 skipped, 1 xfailed after. Unit 1093 passed, 1 skipped. Ruff check and format clean on the new file.
