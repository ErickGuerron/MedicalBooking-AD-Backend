Objective: E5 of the backend plan: basic clinical knowledge without drugs. Validated reference ranges and declarative rules, classification of results at registration, deterministic preliminary proposals (never a diagnosis), and an immutable medical review of each proposal.
Branch: feature/clinical-knowledge-e5 (from develop). Delivery: ask-on-risk. Test runner: uv run pytest (integration needs SQL Server localhost,1433). Risk: item 4 (concurrent validation of overlapping references and concurrent proposal reviews) and item 2 (new clinical permissions).

## Specs
- S1: Spec 1 and 7.2: the knowledge module is deterministic; no LLM, no predictive model. Preliminary findings rely only on previously validated rules; a doctor accepts, corrects or rejects them.
- S2: Spec 7.2 step 2: for each analyte select ONE approved applicable reference; when context is missing do not invent a category: mark NO_EVALUABLE. Also NO_EVALUABLE when no reference applies or more than one does.
- S3: Spec 5.3 and DBML: classification is BAJO, DENTRO_REFERENCIA, ALTO or NO_EVALUABLE (never a diagnosis). Boundaries are inclusive: min <= value <= max is DENTRO_REFERENCIA. An open bound (null) is unbounded.
- S4: Spec 7.2 step 4 and 8.1: store snapshots of value, unit, applied reference id and version, rule version and evidence. Historical interpretations are never recalculated on read.
- S5: Spec 5.4 and DBML note: published references and rules are immutable. Changes create new rows. A reference cannot be validated when an overlapping validated reference exists for the same analyte, unit, sex, age interval, method, sample type, context and validity period (overlap detection, pinned by tests). Overlap checks run under an analyte row lock.
- S6: Spec 6 and 5.3: rules are declarative (operators LT, LE, GT, GE, EQ, FUERA_REFERENCIA, DENTRO_REFERENCIA; logic TODOS or CUALQUIERA). No executable code is stored or evaluated. Only VALIDADA rules run.
- S7: Spec 7.2 step 3: a rule runs only on inputs that are available. Under TODOS a rule with a missing input does not fire; under CUALQUIERA missing inputs simply do not satisfy.
- S8: Spec 7.2 step 4-5: a proposal records the engine version, every finding with its rule version snapshot, its evidence (result id, observed value, unit) and state PENDIENTE. If nothing is classifiable and nothing fires it is SIN_EVIDENCIA.
- S9: Spec 7.2 step 5 and 12: an authorized doctor (the ordering doctor, per E3 S3) records ACEPTAR, CORREGIR or RECHAZAR once per proposal. The proposal content (summary, findings, explanations, rule versions, evidence) is never rewritten. The decision itself goes to propuestas_revisiones, an append-only history. The review updates only the review-state columns the DBML defines (estado, revisado_por, revisado_en, conclusion_medica and the finding estado_revision). A second decision conflicts with 409 (verifier advisory A1 accepted with this reading).
- S10: Permissions: conocimiento.leer (medico, laboratorista, administrador) reads validated knowledge; conocimiento.administrar (administrador) creates analytes, reference drafts and rule drafts; conocimiento.validar (medico) validates them. Evaluating and reading a proposal requires resultados.leer_con_acceso and ordering-doctor ownership; reviewing requires revisiones.registrar and ownership.
- S11: Migration 0006 seeds synthetic knowledge only: one synthetic source, two analytes linked to the E3 test types, one validated reference per analyte, and two validated rules (HB-BAJA-SINT on hemoglobin, GLU-FUERA-SINT on glucose, the latter as a FUERA_REFERENCIA criterion). It also adds a synthetic validator user without credentials. All rows are marked SINTETICO. Corrected from "one rule" by the verifier's B3: the spec now matches the seed and the second rule is tested end to end.
- S16 (added after verification): a numeric criterion must carry the unit of its threshold; without one it never runs and is refused on creation. A result cannot reference an unknown analyte (422, nothing stored). One proposal per order is enforced by a unique index as well as by the order lock.
- S12: Audit: evaluation, reads of proposals and reviews write auditoria_accesos rows with outcome (denied ones too), as in E3.
- S13: ASSUMPTION: the E3 result endpoint accepts optional analito_id, metodo_analitico and tipo_muestra. Results without analito_id are stored unclassified (interpretacion NULL), as before. The E3 contract is otherwise unchanged.
- S14: ASSUMPTION: one proposal per order in E5 (a second evaluation conflicts with 409). Versioned re-evaluation is deferred.
- S15: Spec 13 DoD: endpoints have permission checks, audit, unit and integration tests, error coverage, OpenAPI and docs.

## Tasks
- T1 | S10, S11 | inline | done | migration 0006: permissions, role matrix, synthetic seed; unique index on propuestas_clinicas(orden_id) (S16). Commit: pending
- T2 | S2, S3, S5 | inline | done | domain references: applicability, classification, overlap with wildcards. RED observed (module missing), then GREEN: 26 tests. Commit: pending
- T3 | S4, S13 | inline | done | results classified at registration against the validated reference (laboratory service); unknown analyte refused (S16). RED observed (2 failing), then GREEN. Commit: pending
- T4 | S5, S10 | inline | done | knowledge administration: analytes, reference drafts, validation with overlap lock, rule drafts and validation. Integration (parallel validation keeps one). Commit: pending
- T5 | S6, S7, S8 | inline | done | evaluation engine: rules, inputs, findings, evidence (one row per result), states. RED observed, then GREEN: 36 domain tests with T2. Commit: pending
- T6 | S8, S14 | inline | done | evaluate and read proposal: one per order, order lock plus unique index; integration (parallel evaluation keeps one). Commit: pending
- T7 | S9, S12 | inline | done | review of proposal: once, append-only history, ownership, denials audited including missing permissions. Integration (parallel review keeps one). Commit: pending
- T8 | S15 | inline | done | routes, OpenAPI fragment (20 paths, 28 schemas), docs/api/openapi.json regenerated, barrels for clinical domain and application. README and deployment notes: pending. Verifier run after T5-T7 (risk items 4 and 2).

Route: all inline. Deviation disclosed: integration tests were written after the SQL adapter, so their RED was not observed; their domain and application parts had RED observed.

## Log
- L1: "necesito que crees nuevas ramas siguiendo el flujo de git flow para el repo y hay si contineus con el E5" (user). Branches: main (base), develop (integration, equals origin/develop at 033eeda), feature/lab-orders-results-e3 (E3 commits 136392a and 72e0ab7, not yet merged into develop), feature/clinical-knowledge-e5 (this branch, from develop).
- L2 (evidence, schema): the DBML tables conocimiento_*, propuestas_* and their CHECKs already exist in migration 0001, including the composite FK from resultados_laboratorio to conocimiento_referencias(id, version). E5 adds permissions and seeds, not tables.
- L3 (evidence, baseline): 213 tests pass on feature/lab-orders-results-e3 before E5.
- L4 (git-flow): E3 merged into develop with --no-ff (328a974, local, not pushed). feature/clinical-knowledge-e5 created from develop. develop was moved from the old local E3 state to origin/develop first; the E3 commits were kept on feature/lab-orders-results-e3 until the merge.
- L5 (evidence, final before verification): 272 tests pass in the full suite after E5 (210 clinical unit+integration among them). One pre-existing test (LABORATORISTA permission matrix) was intentionally updated: E5 grants conocimiento.leer (S10).
- L6 (verifier, read-only, risk items 4 and 2): concurrency and authorization held. Findings and treatment:
  - B1 (fixed): two criteria on one analyte duplicated the evidence row for the same result and broke the evidence key (500). One evidence row per result now (pinned by test_two_criteria_on_one_analyte_record_one_evidence_per_result).
  - B2 (fixed): an unknown analyte in a lab result reached a foreign key and returned 500. Now 422, nothing stored (pinned by test_unknown_analyte_in_a_lab_result_is_422_and_stores_nothing and a unit test).
  - B3 (fixed in the spec): the seed has two validated rules; S11 now says so, and GLU-FUERA-SINT is tested end to end.
  - A2 (fixed): unique index on propuestas_clinicas(orden_id) (S16; pinned by test_the_database_keeps_one_proposal_per_order).
  - A3 (fixed): numeric criteria need a unit; without one they never compare raw numbers (S16; unit and integration tests).
  - A1 (accepted with reading): review updates review-state columns only (S9 clarified).
  - A4 (accepted, open): denials of knowledge administration (analytes, drafts, rules) are refused but not audited. S12 covers clinical records, not the catalog.
  - A5 (open): a lab result may reference any active analyte, not only analytes linked to its order's test type (conocimiento_pruebas_analitos). Not enforced in E5; recorded for a later phase.
  - Verifier scratch: a copy containing the local .env was created under TEMP; removed after the run.
- L7 (evidence, after fixes): unit 192 passed; clinical integration 26 passed; full suite and lint pending final run.
