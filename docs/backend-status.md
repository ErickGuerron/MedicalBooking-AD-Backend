# Backend status

This page records what is delivered, what is blocked and why, and the decisions taken, phase by phase
(spec section 11). It is updated with each phase. Commit hashes refer to `develop`.

Last verified: 305 tests passing, `ruff check` and `ruff format --check` clean.

## Delivery by phase

| Phase | Scope | Status | Evidence |
|---|---|---|---|
| E0 | Repository, Docker, configuration, migrations, local SQL Server | Done, except Docker verification of T7 and Azure SQL migration verification (T8) | `backend-foundation` feature document |
| E1 | Identity, roles, permissions, ownership, lockout after 5 failures | Done | `2ab0fa5`, `806fe24`, `identity-e1` document |
| E2 | Schedules and appointments: availability, idempotent booking, cancellation | Done | `41379ea`, `c80d756`, `scheduling-e2` document |
| E3 | Laboratory: orders, manual results, medical review, PDF documents | Done | `136392a`, `72e0ab7`, `lab-orders-results-e3` document |
| E4 | Payment hold expiration worker | Done. Payment checkout and webhook are **not** implemented (see Blocked) | `1ae6b50`, `fd05fa0`, `payment-expiration-e4` document |
| E5 | Clinical knowledge: ranges, rules, proposals, medical review | Done | `d598fb6`, `6dcdaf7`, `a25db27`, `3e07b29`, `clinical-knowledge-e5` document |
| E6 | SOAP service `/soap/laboratory?wsdl`, OpenAPI, observability | OpenAPI done for every module. SOAP **not started** (waiting for approval of a dependency or a hand-written WSDL) | `docs/api/openapi.json` |
| E7 | Azure SQL, 4 instances on 2 VMs, load balancer, HTTPS | Not started. Needs Azure resources | `docs/deployment.md` |
| E8 | Outbox to Supabase and MongoDB Atlas, read-only contingency | Not started. Needs Supabase and Atlas accounts | — |
| E9 | Report, load and failure experiments, documentation | Partial: this documentation set and the experiment plan in `deployment.md` | `docs/` |
| E10 | openFDA (optional, second phase) | Out of scope by decision | — |

Cross-cutting work delivered outside the phase list:

- **Container fix (`bcdc52b`, PR #4):** gunicorn workers failed to boot because the storage folder
  was created under the root-owned `/app`. The folder is now created on first upload, and the
  image provides `/app/data/documents` owned by the application user.
- **External knowledge importers (`7377cb7`, branch `feature/knowledge-importers`):** LOINC,
  MedlinePlus Connect, PubMed and ICD-11 as drafts, all or nothing. See `knowledge-import.md`.

## Blocked, and what unblocks each item

| Item | Needs | Who provides it |
|---|---|---|
| Stripe Sandbox checkout and verified webhook (E4 payments) | Test secret key and webhook signing secret | Owner of the Stripe account |
| Brevo notifications (E4) | API key | Owner of the Brevo account |
| Azure SQL, VMs, load balancer, HTTPS (E7) | Azure subscription and resources | Owner of the Azure subscription |
| Supabase read replica and MongoDB Atlas (E8) | Project URLs and credentials | Owner of the accounts |
| ICD-11 codes for rules | Published rule needs a new draft version, then a doctor's decision | Clinical reviewer |
| Literature (PubMed PMIDs) per rule | Choice of supporting studies | Clinical reviewer |
| Validation of MedlinePlus drafts | Clinical review of the educational text | Clinical reviewer |

## Database state

- Migrations `0001` to `0006` are the full history. The local development database
  (`MedicalBooking`) was upgraded to `0006` on 2026-11-02 for the knowledge import.
- Test database `MedicalBooking_test` is recreated from zero at the start of each test session.

## Decisions taken (short list)

These are recorded in each feature document with their rationale. The most important ones:

- Results are classified only against a validated reference applicable to the patient. When context
  is missing the category is `NO_EVALUABLE`; no category is invented.
- A result must belong to the test it is entered under, and a test completes only when every
  mandatory analyte (`obligatorio = 1`) has a result (E5 S16, S17).
- Published knowledge is immutable. Imports and edits create drafts; a doctor with
  `conocimiento.validar` publishes them.
- External sources never write clinical decisions. They provide terminology, educational text,
  evidence identity and hypothesis codes, each with its provenance.
- A denied access is audited with its outcome, including a missing permission (S12).
- Missing and foreign resources answer the same 403, so identifiers do not leak.

## Open advisories

- **A4 (accepted):** denials on knowledge administration (analytes, drafts, rules) are refused but
  not audited. The audit requirement covers clinical records, not the catalog.
- **Document storage is local:** PDFs live on the disk of the host that received them. Two VMs
  would not see each other's files, so shared storage is required before E7 (`deployment.md`).
- **No streaming for downloads:** a PDF is read fully before it is sent (maximum 10 MB).
- **Docker run not verified end to end against SQL Server:** the image starts and serves
  `/health/live`; database-backed routes were verified in the test suite, not in the container.
