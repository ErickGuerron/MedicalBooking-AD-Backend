# Historia clínica: alergias verificadas, antecedentes, medicación, problemas, vacunas y signos vitales (backend)

Objective: a clinical history per patient that a treating doctor and the patient can use, where safety-critical data (allergies) is verified by a professional, is never deleted, and every change and every access is traceable. Branch: feat/historia-clinica (from develop). Delivery: ask-on-risk. Test runner: uv run pytest (integration needs SQL Server on localhost,1433 or the port in SQLSERVER_PORT).

## Specs

S1. Allergies (alergias): substancia (1-150 characters), categoria (MEDICAMENTO, ALIMENTO, LATEX, AMBIENTAL, OTRA), reaccion (optional, max 300), severidad (LEVE, MODERADA, GRAVE, DESCONOCIDA). Status estado: ACTIVA, RESUELTA, ERRONEA.
S2. A treating doctor who registers an allergy makes it verified at once (origen REGISTRADA_MEDICO, verificada=1, verificada_por, verificada_en).
S3. A patient may declare their own allergy (origen DECLARADA_PACIENTE, verificada=0). A declared allergy is shown as declared until a treating doctor verifies it. The patient cannot verify.
S4. A treating doctor verifies a declared entry: it becomes verificada=1 with the doctor and the time. Verifying twice is refused with 409.
S5. Changes of state never delete. Every change of state needs a motivo (1-500 characters), including reactivations. ERRONEA is final; a transition not declared per kind is a conflict. Every change appends one event to historia_eventos with the values before and after.
S6. Antecedentes (personal, familiar, quirurgico): descripcion (1-500), parentesco (1-50, required only for familiar), cie11 (optional, CIE-11 code), estado ACTIVO, RESUELTO or ERRONEO. Same rules as S2 to S5.
S7. Medicación actual: medicamento (1-150), dosis (max 80), frecuencia (max 80), inicio (optional date). estado ACTIVA, SUSPENDIDA or ERRONEA. Same rules as S2 to S5.
S8. Problemas activos: descripcion (1-300), cie11 (optional), inicio (optional date). estado ACTIVO or RESUELTO. Same rules as S2 to S5.
S9. Vacunas: vacuna (1-120), dosis_numero (1-10), fecha (required, not in the future), lote (optional, max 40). A doctor registers (verified); a patient declares (not verified). Same rules as S3 to S5.
S10. Signos vitales are append-only observations tied to one consultation of the caller's agenda. At least one measure is required. Ranges: peso_kg 0.5-300, talla_cm 30-250, pa_sistolica 50-260, pa_diastolica 30-160, frecuencia_cardiaca 20-250, temperatura_c 30-45, saturacion 50-100. Corrections are new entries with a motivo; existing entries never change.
S11. Who reads and writes (every decision is checked in this order: permission, then relation, then the resource):
  - Treating doctor: a MEDICO with at least one cita with the patient in any state (past or future). Reads the full history and writes the entries of S1 to S9 and S10 for their own citas.
  - The patient: reads their own history and declares allergies, antecedentes, medicación, and vacunas. Never verifies, never writes vitals, never writes problemas.
  - Everyone else (LABORATORISTA, ADMINISTRADOR, a MEDICO without a cita with the patient, a PACIENTE reading another patient): 403, audited as DENEGADO.
S12. A missing patient, a missing entry and a foreign entry all answer the same 403, so identifiers do not leak.
S13. Access to the history is audited as CONSULTAR. Writes are audited as CREAR, ACTUALIZAR, VERIFICAR, or REGISTRAR. The audit never stores clinical values, only entity, identifier, action and result.
S14. GET /api/v1/patients/{paciente_id}/historia returns the entries grouped by type. Active allergies come first and carry their verification status, so a consultation can show them at the top.
S15. Status changes are guarded: a change applies only if the entry is still in the state the caller read (expected_estado). Otherwise 409. This prevents two doctors overwriting each other.
S16. Permissions seeded by migration: historia.leer (MEDICO), historia.registrar (MEDICO), historia.declarar (PACIENTE), historia.leer_propia (PACIENTE). No other role receives them.
S17. Validation failures are 422 (ValidationFailed), as in the rest of the project.
S18. The OpenAPI contract and docs/api/openapi.json are updated before the work is considered done (AGENTS.md: contrato primero).
S19 (superseded in part by L8). Known gaps stated up front: the event log is written but not yet exposed through an endpoint (auditors read it in SQL); the Flutter portal does not yet show the patient's own history; CIE-11 codes are validated by format only, not against the catalog; the vital-sign read does not yet filter by date range.

## Tasks

T1. Migration 0045: tables, checks, permissions and roles — S1-S10, S16 — route: inline — status: done (migration 0045, applied in the test chain) — commit: slice 1
T2. Domain rules: states, lengths, ranges, verification and transitions, with unit tests — S1-S10, S17 — route: inline — status: done (87 history unit tests with T3) — commit: slice 1
T3. Application service: relation check, permissions, audit, guarded transitions, with unit tests on an in-memory store — S2-S5, S11-S13, S15 — route: inline — status: done (service and in-memory unit tests) — commit: slice 2
T4. SQL store and routes for allergies, antecedentes, medicación, problemas, vacunas — S1-S9, S12, S14 — route: inline — status: done (SQL store, routes; integration verified) — commit: slice 3
T5. Vital signs and the history read model — S10, S14 — route: inline — status: done (vital signs, corrections, history read model) — commit: slice 3
T6. OpenAPI fragment and docs snapshot — S18 — route: inline — status: done (OpenAPI fragment, contract test, docs snapshot) — commit: slice 3
T7. Integration tests against SQL Server: access matrix, guarded transitions, append-only, audit — S11-S15 — route: inline — status: done (15 integration tests against SQL Server) — commit: slice 3

## Log

L1 (user, verbatim): "vamos con todo lo que dices para hacerlo distinto a un generico dale con este flujo completo no importa si sea un cambio tan grande pero mejor hagamoslo primero a nivel de backend"
L2 (decisions by the assistant, to be reviewed): one module `history`; typed tables per entry kind plus one append-only `historia_eventos`; the relation between doctor and patient is any cita in any state; the patient's own declarations are never verified by the patient.
L3 (evidence so far): the treatments module supplies the pattern (treating-doctor check, denial audited without values, 403 for missing and foreign resources). The clinical profile module supplies the patient-declared pattern.
L4 (decision, S5): the motivo is required for every state change, not only for resolutions, because a reactivation is also a clinical fact that needs a reason.
L5 (decision, S10): vital signs are numbers in the API (Decimal converted at the HTTP edge), not strings; money keeps its string convention elsewhere.
L6 (fixes found while building): vacunas had no estado column, so an erroneous vaccine could not be marked without deleting it; added in migration 0045. The service now checks the patient in the path before verifying or changing a state (S12). Pydantic strict mode on the whole model refused ISO dates, so strictness is per field.
L7 (evidence): migration 0045 applies in the test fixture chain; 87 history unit tests and 1818 backend unit tests pass; 15 history integration tests pass against SQL Server on port 1270, including the append-only trigger and the access audit; the OpenAPI contract test (40) passes with the new paths.
L8 (second iteration, user request): catalogs, date ranges, the event log and the patient's own history.
  - Catalogs (migration 0046): catalogo_cie11 (starter seed), catalogo_vacunas, catalogo_sustancias_alergia. Allergies take sustancia_codigo and vacunas take vacuna_codigo; the name and category come from the catalog and are kept as a snapshot. Antecedentes and problemas accept cie11 only when it is an active catalog code. The full CIE-11 list is NOT loaded: it needs an import from the official source, which is a gap.
  - Date ranges: GET historia and the event log take desde and hasta (inclusive days, UTC). At most 366 days apart; hasta before desde is refused.
  - Event log: GET /patients/{id}/historia/eventos with entidad, accion, desde, hasta, limit (default 50, max 200) and offset. The treating doctor sees the actor; the patient sees the change without the actor identity. Newest first, with a total.
  - Patient's own: GET /me/historia and GET /me/historia/eventos, so Flutter does not need to know its patient id.
  - Catalog search (any signed-in person): GET /catalogos/cie11, /catalogos/vacunas, /catalogos/sustancias-alergia with q and limit. Wildcards typed by a person are matched literally.
  - Fixes found while testing: a patient id returned in upper case by SQL Server now comes back lower case everywhere (own history); a decimal in vital signs comes back as a number.
  - Evidence: 1842 backend unit tests pass; 49 history, catalog and sedes integration tests pass against SQL Server on port 1270; the OpenAPI contract test passes with the new paths.
  - Gaps still open: the full CIE-11 catalog import; a catalog admin screen; the patient's Flutter screen; the Angular staff screen. The repository has no ICD-11 or vaccine catalog before this change, so the seed is a starter set.
