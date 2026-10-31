Objective: E0 of MedicalBooking backend. Schema from the DBML on local SQL Server, Flask factory, health probes.
Branch: feat/backend-foundation (repo has no prior commits; work starts on feature branch).
Delivery: ask-on-risk. Test runner: uv run pytest (integration needs SQL Server localhost,1433 default instance).

## Specs
- S1: "necesito que comencemos con la implementacion del backend con respecto a la estructura del dbml, para la base de datos local de SQL serer (al ultimo se conectara con la de produccoon para ahorrar costos  y la replicaion aora supabase y Atlass con mongo"
- S2: "en la por defecto en localhost:1433,la 1" (user choice: default SQL Server instance, option 1, new database MedicalBooking; Windows authentication used locally)
- S3: Modular monolith with hexagonal architecture and clean architecture (spec: presentation -> application -> domain; infrastructure implements ports).
- S4: Source of truth is SQL Server locally, Azure SQL later. Same migrations for both. Never create_all().
- S5: The DBML is converted to T-SQL and reviewed: PK, FK, indexes, CHECK, datetime2, uniqueidentifier, decimal, rowversion.
- S6: Filtered unique index on active appointments per schedule slot (spec 5.2).
- S7: CHECK constraints for dates (fin > inicio), non-negative money, state domains, value ranges (spec 5.3).
- S8: Immutable knowledge versions enforced by composite FKs (id, version) (spec 5.4).
- S9: Health probes: /health/live without dependencies; /health/ready checks the database, no sensitive details (spec 6, 10).
- S10: Errors as JSON problem with correlation_id (spec 6).
- S11: Secrets only from .env or environment, never committed (spec 8.1, 10).
- S12: Assumption: state domains not defined by the DBML are marked ASSUMPTION in the migration and need clinical/product sign-off.

## Tasks
- T1 | S1, S2, S4 | inline | done | repo, pyproject (Python 3.12), Ruff, pytest, README
- T2 | S5, S6, S7, S8 | inline | done | migrations/sql/0001_initial_schema.sql (generated from DBML with dbml2sql, then corrected by hand)
- T3 | S4, S5 | inline | done | Alembic baseline 0001 and idempotent seed 0002 (roles, sync targets)
- T4 | S6, S7, S8 | inline | done | integration tests for constraints (tests/integration/test_schema_constraints.py)
- T5 | S3, S9, S10, S11 | inline | done | config validation, app factory, health probes, problem+json errors
- T6 | S11 | inline | blocked | .env.example not created: the editing tool denies `.env.*` files by permission rule. Variables documented in README.md instead.
- T7 | S1 | inline | pending | Docker Compose and Dockerfile (E0 criterion); Docker Desktop is not running locally.
- T8 | S4 | inline | pending | Azure SQL migration verification (E7).

## Log
- L1: "necesito que comencemos con la implementacion del backend con respecto a la estructura del dbml, para la base de datos local de SQL serer (al ultimo se conectara con la de produccoon para ahorrar costos  y la replicaion aora supabase y Atlass con mongo, ademas  de ello tner muy en cuenta lo que dicen los mds para que tengas en cuenta el contexto del proeycto y la arqutiectura que definimos que es un monolito modilar + arquitectura hezagonal con clean arquitecture"
- L2: "en la por defecto en localhost:1433,la 1"
- L3 (evidence): dbml2sql draft had no filtered index, no CHECK, no composite FK. Corrected in 0001.
- L4 (defect found by integration test): plain UNIQUE on nullable columns admits only one NULL in SQL Server. Fixed with filtered unique indexes on usuarios.auth_provider_id, facturas.numero_factura, pagos.stripe_payment_intent_id, pagos.stripe_checkout_session_id, reembolsos.stripe_refund_id, conocimiento_analitos.codigo_loinc. Regression test added.
- L5 (evidence): dev DB MedicalBooking was dropped and recreated once, while empty, after L4 changed the baseline (not yet released). Migration reapplied from zero: 51 tables, head 0002_seed_reference, 4 roles, 8 filtered indexes.
- L6 (evidence): uv run pytest -> 21 passed, coverage 96%; ruff check and ruff format --check clean; /health/ready returned 200 against MedicalBooking.
- L7: Deferred to E5: immutability trigger for published knowledge rows and overlap detection for reference ranges (needs trigger or service check; SQL Server has no exclusion constraint).
- L8: Deferred to E1: permission catalog seed (permisos, roles_permisos).
