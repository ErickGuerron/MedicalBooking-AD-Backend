Objective: E3 of the backend plan: laboratory order -> manually entered results -> medical review, plus an uploaded PDF report with private storage. Acceptance (spec section 11): flow order -> result -> review and a test PDF.
Branch: develop. Delivery: ask-on-risk. Test runner: uv run pytest (integration needs SQL Server localhost,1433). Risk: item 2 (security: file upload, ownership on clinical data) for the documents unit.

## Specs
- S1: User choice for E3 PDF: "quisiera que para aprovechar la estructura de la bd que esta definida en el .dbml que se pueda subir el .pdf e ingresar manualmente". Results are entered by hand; the PDF is stored and downloaded, never parsed.
- S2: Spec 6 contracts: POST /api/v1/lab/orders, GET /api/v1/lab/orders/{id}, POST /api/v1/lab/orders/{id}/results, GET /api/v1/lab/orders/{id}/results, POST /api/v1/lab/orders/{id}/documents, GET /api/v1/documents/{id}/download. POST .../review is the medical review (spec 7.2 step 5 applied to the order, since proposals belong to E5).
- S3: Spec 6 authorization: every business endpoint requires JWT and per-resource authorization. Patient sees only own orders, results and documents (resultados.leer_propios). The ordering doctor sees the orders they created (resultados.leer_con_acceso). Missing and forbidden resources return the same 403 so identifiers do not leak (same rule as E2).
- S4: Spec 7.2: results are recorded with permission control, test type, units and synthetic data. Results are never edited to hide changes; a review does not rewrite results.
- S5: Spec 7.2 step 5 and 12: the review (ACEPTAR, CORREGIR, RECHAZAR) is registered by an authorized doctor once per order; a second review conflicts with 409. Only the ordering doctor may review (ASSUMPTION: the ordering doctor is the authorized reviewer).
- S6: Spec 8.1 and DBML note: documentos_clinicos stores a storage reference, hash and metadata; the binary is never stored in the database, never a public URL. Downloads stream from private storage with the hash re-checked.
- S7: PDF upload accepts only application/pdf content with a %PDF- signature, size limit 10 MB, file name sanitized and never used as a storage path (storage name is a generated UUID).
- S8: Spec 7.2: laboratory results have no clinical interpretation in E3: interpretacion, referencia_min, referencia_max stay NULL until E5 defines ranges (ASSUMPTION). A result never claims a diagnosis.
- S9: Order states follow the schema: SOLICITADA -> EN_PROCESO (first result) -> COMPLETADA (every detail completed). Detail: PENDIENTE -> COMPLETADO. Review: revisiones_laboratorio PENDIENTE -> COMPLETADA.
- S10: Test types: the DBML table tipos_prueba_laboratorio has no seed yet. Migration 0005 seeds a few synthetic test types idempotently (codes marked SINTETICO in the description).
- S11: Audit: reads of results and document downloads and every clinical write record auditoria_accesos rows with actor, action, entity, outcome and correlation id, without clinical values.
- S12: ASSUMPTION (recorded for E7): the document storage is a local private directory (LAB_DOCUMENTS_DIR). Replicas on two VMs do not share a local disk; Azure Blob or a shared volume is required before E7 multi-VM deployment. Until then uploads work only on a single host.
- S13: Spec 13 DoD: each endpoint has permission checks, audit, unit and integration tests, error coverage and documentation (README and OpenAPI).

## Tasks
- T1 | S5, S8, S9, S10 | inline | done | migration 0005 synthetic test types (idempotent MERGE). Commit: pending
- T2 | S2, S3, S9 | inline | done | orders: create and read with ownership; RED observed (unit import error), then GREEN; SQL adapter; integration. Commit: pending
- T3 | S4, S8, S9, S11 | inline | done | results: manual entry, order state transitions (EN_PROCESO, COMPLETADA), parallel-write test (8 parallel, 1 wins). Commit: pending
- T4 | S5, S9 | inline | done | medical review: once per order after completion, parallel test (6 parallel, 1 review). Commit: pending
- T5 | S6, S7, S12, S11 | inline | done | documents: PDF policy, private storage (opaque key, no path escape), SHA-256, download with integrity check and audit. Commit: pending. Risk item 2 named.
- T6 | S2, S13 | inline | done | routes, app wiring, OpenAPI fragment (12 paths total), README and deployment docs, openapi.json regenerated. Commit: pending
- T7 | S13 | inline | done | independent read-only verifier on documents (risk item 2): 2 blockers (S11 correlation id missing; denied reads not audited) fixed and pinned by tests; advisories recorded in the Log.

Route: all inline. Deviation disclosed: the documents unit and the orders/results units share use_cases.py, repositories.py and routes.py, so they could not be split into separate commits without hunk-level staging. The code ships as one commit; the verifier reviewed the documents unit on its own.

## Log
- L1: "quisiera que para aprovechar la estructra de la bd que esta definida en el .dbml que se peuda subir el .pdf e ingresar manualmente , te parece bien ?" (user answer on the E3 PDF scope). Assistant agreed: upload plus manual entry, no PDF generation and no new PDF dependency.
- L2 (evidence, baseline): uv run pytest -> 162 passed on develop at 033eeda before E3.
- L3 (evidence, schema): CK_ordenes_estado, CK_orden_detalle_estado, CK_revisiones_estado, CK_resultados_* and CK_documentos_* exist; documentos_clinicos has no estado domain check (ASSUMPTION: DISPONIBLE after upload).
- L4 (evidence, schema): auditoria_accesos columns: usuario_id, paciente_id, entidad, entidad_id, accion, fecha_evento, ip_origen, resultado, correlation_id, motivo_acceso.
- L5 (correction of S5): revisiones_laboratorio has no decision column. ACEPTAR, CORREGIR and RECHAZAR apply to clinical proposals (E5). E3 records a conclusion and recommendations on the order, once, only after completion.
- L6 (evidence, final): uv run pytest -> 213 passed (162 baseline + 51 new); ruff check and ruff format --check clean; docs/api/openapi.json regenerated with 12 paths.
- L7 (verifier blockers, fixed and pinned): B1 audit rows had no correlation_id (now set from the request via the composition root; pinned by test_audit_carries_the_request_correlation_id and test_download_audit_has_the_patient_and_the_request_correlation_id). B2 denied reads were not audited and always recorded OK (now DENEGADO, INTEGRIDAD_FALLIDA on tamper; pinned by test_denied_document_download_is_audited_with_its_outcome).
- L8 (advisories, recorded): A1 a body starting with %PDF- is accepted without full structure checks; the download is served as an attachment with Content-Disposition, not inline. A2 the download reads the whole file before sending (no streaming); the hash must be checked first, so memory grows with file size up to 10 MB. A3 a laboratorist uploading to a missing order gets 404 (S3 covers reads only). A4 a crash between file write and DB insert leaves an orphan file. A5 the default LAB_DOCUMENTS_DIR is relative to the working directory. A6 symlink escape is blocked by path resolution in code; not exercised at runtime (no symlink privilege on this machine).
- L9 (open): multi-VM deployment needs shared document storage before E7 (see docs/deployment.md). Docker spot check of the upload path not run in this phase.
