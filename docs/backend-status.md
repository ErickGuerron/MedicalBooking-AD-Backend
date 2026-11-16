# Backend status

This page records what is delivered, what is blocked and why, and the decisions taken, phase by phase
(spec section 11). It is updated with each phase.

Last reviewed: 2026-11-16, against the live Azure environment (read-only checks; nothing was changed).
The test suite was not re-run for this review. Last recorded result: 1054 tests passing (1 xfail).

## Delivery by phase

| Phase | Scope | Status | Evidence |
|---|---|---|---|
| E0 | Repository, Docker, configuration, migrations | Done | `backend-foundation` feature document |
| E1 | Identity, roles, permissions, ownership, lockout after 5 failures | Done | `2ab0fa5`, `806fe24`, `identity-e1` document |
| E2 | Schedules and appointments: availability, idempotent booking, cancellation | Done | `41379ea`, `c80d756`, `scheduling-e2` document |
| E3 | Laboratory: orders, manual results, medical review, PDF documents | Done in code. No orders or results exist yet in the cloud database | `136392a`, `72e0ab7`, `lab-orders-results-e3` document |
| E4 | Payments (checkout, Stripe webhook), patient notifications (handler, retry worker, Brevo adapter) | Implemented. Stripe Sandbox and real Brevo sends are not verified. Notifications worker runs on VM1 | `1ae6b50`, `fd05fa0`, `34bd804`, `06be8b1`, `payment-expiration-e4` document |
| E5 | Clinical knowledge: ranges, rules, proposals, medical review | Done | `d598fb6`, `6dcdaf7`, `a25db27`, `3e07b29`, `clinical-knowledge-e5` document |
| E6 | SOAP service `/soap/laboratory?wsdl`, OpenAPI, observability | Implemented: WSDL and `GetLaboratoryResultStatus`. OpenAPI for every module. `/health/live` reports the machine name | `b389398`, `202c9df`, `38415d2`, `docs/api/openapi.json` |
| E7 | Two Azure VMs, four replicas, load balancer, DuckDNS, HTTPS | Done and running (see "Production topology") | Verified 2026-11-16, see below |
| E8 | Outbox to Supabase and MongoDB Atlas, read-only contingency | Outbox to both replicas is delivering. Automatic sync is scheduled on VM1. Contingency path not yet exercised with Azure SQL down | `f5362d1`, `69b2ff5`, `b2753a0`, `replica-watermark-sync`, `mongo-projection` documents |
| E9 | Report, load and failure experiments, documentation | Partial. This page. Experiments are not written yet | `docs/` |
| E10 | openFDA (optional, second phase) | Out of scope by decision | - |

Kafka was evaluated as a replication broker and discarded as over-engineering. The outbox dispatcher
is the only replication path. The `KAFKA` destination row is still present in the database and must be
retired (see Open items).

## Production topology (verified 2026-11-16)

| Component | Value | Check |
|---|---|---|
| Public name | `medilo-backend.duckdns.org` resolves to `52.162.216.123` (Azure Load Balancer) | DNS lookup |
| VM1 | `MediloVM1`, public IP `52.162.33.189`, replicas `api-1` and `api-2`, Nginx balancer on 80/443 | SSH, `docker ps`, health through Nginx |
| VM2 | `MediloVM2`, public IP `135.232.219.176`, replicas `api-3` and `api-4`, Nginx balancer on 80/443 | SSH, `docker ps`, health through Nginx |
| Image | `meliodasdev/medicalbooking-backend:1.2.2` on both VMs | `docker image ls` |
| TLS | Let's Encrypt certificate for `medilo-backend.duckdns.org`, issuer `YE2`, valid until 2027-02-13 (GMT) | `openssl` from the VM and an external HTTPS handshake |
| Health | `GET /health/live` and `/health/ready` return 200 through the load balancer | external requests |
| Distribution | 30 new connections through the balancer: `api-1` 6, `api-2` 6, `api-3` 9, `api-4` 9 | external requests with `--no-keepalive` |

Notes on the distribution check:
- Keep-alive requests from one client stay on one backend. An earlier 20-request run that reused the
  connection reached only VM2. Separate connections reach all four replicas. Use new connections when
  testing balancing.
- A failover test (stopping a replica or a VM while under load) was not part of this review.

## Scheduled jobs (crontab)

| Host | Schedule | Job |
|---|---|---|
| VM1 | every minute | outbox dispatcher (delivers to Supabase, MongoDB, notifications) |
| VM1 | every minute | notifications worker |
| VM1 | every 15 minutes | external-write sync (`sync_external_writes`) |
| VM1 | every 15 minutes | replica reconciliation (`reconcile_replicas`) |
| VM1 | every 15 minutes | replica catalog sync (`sync_replica_catalog`) |
| VM1 | every 5 minutes | release deploy with migrations (`release-deploy.sh --migrate`) |
| VM2 | every minute | outbox dispatcher |
| VM2 | every 5 minutes | release deploy (replicas only) |

The workers run on VM1 only, so two schedulers do not queue the same rows twice. Logs are in
`~/logs` on each VM.

## Data state (row counts, 2026-11-16)

Counts were read directly from each database with an owner or writer role. The Supabase reader role
(`replica_reader`) sees 0 rows in `usuarios`, `pacientes` and `citas` because of row-level security
without a user context. That is by design for the contingency path, not missing data.

| Entity | Azure SQL | Supabase (`replica`) | MongoDB Atlas (`Medilo`) |
|---|---|---|---|
| usuarios | 11 | 11 | 11 |
| pacientes | 6 | 6 | 6 |
| citas | 6 | 6 | 6 |
| medicos | 2 | 2 | 2 |
| servicios | 4 | 4 | 4 |
| permisos / roles | 23 / 4 | 23 / 4 | - |
| pagos, facturas | 0 | 0 | - |
| ordenes_laboratorio, resultados_laboratorio | 0 | 0 | - |
| tratamientos | 0 | 0 | - |
| documentos_clinicos | 0 | - | - |

Outbox: 74 events (`cita.actualizada` 24, `usuario.creado` 21, `cita.reservada` 12,
`usuario.eliminado` 11, `cita.eliminada` 6). Deliveries: 74 `ENTREGADO` to `SUPABASE`, 74 to `MONGODB`,
74 to `NOTIFICACIONES`, and 28 to `KAFKA` (inactive destination).
MongoDB also holds `auditoria_accesos` (5), `eventos_procesados` (74) and `tombstones` (17).

## Open items

1. **Reconciliation mismatch: resolved.** `reconcile_replicas` reported `repairs_mongodb=1` in every
   cycle. Cause: the validator user `5E5E5E5E-…0005` was projected to MongoDB with an upper-case `_id`,
   while the reconciler compares lower-case ids. The projection now writes lower-case aggregate ids
   (`mongo_projection.canonical_id`), and the one existing document was moved to its lower-case key
   (2026-11-16, backup kept outside the repository). A read-only replay of the planner now reports 0
   repairs. Confirm `repairs_mongodb=0` in `reconcile.log` on VM1 after the next release.
2. **Clinical flow has no data.** No payments, invoices, lab orders, results, treatments or clinical
   documents exist in Azure SQL. The "full flow" check (appointment, payment, exam, treatment, results)
   has no evidence yet.
3. **Contingency not proven in the cloud.** Supabase holds the data, but the read path during an Azure
   SQL outage was not tested against the deployed system. It must be run with Azure SQL unavailable.
4. **Retire the `KAFKA` destination.** It is inactive, yet it has 28 delivered rows. Remove the
   destination, its handler and its configuration, and update the spec and the `kafka-replication` document.
5. **Document storage is per VM.** Uploaded PDFs live in each container's `/app/data/documents`.
   Downloads through the load balancer can therefore fail when the file is on the other VM. Shared
   storage is needed before the document flow is demonstrated (`documentos_clinicos` is empty today).
6. **Experiments not written (T9).** Load tests (one API against four, concurrent requests), replica
   failure and VM failure are not yet recorded under `docs/experiments/`.
7. **Credential hygiene.** VM passwords and the DuckDNS token were pasted into a chat session. Rotate
   them. Do not store them in files, memory or documents.
8. **Operations note.** `docker compose ps` on a VM fails unless `MEDICALBOOKING_IMAGE` is exported, because
   the compose files require it. The release script sets it; manual checks must set it too.

## Blocked, and what unblocks each item

| Item | Needs | Who provides it |
|---|---|---|
| Stripe Sandbox checkout and verified webhook (E4) | Test secret key and webhook signing secret | Owner of the Stripe account |
| ICD-11 codes for rules | Published rule needs a new draft version, then a doctor's decision | Clinical reviewer |
| Literature (PubMed PMIDs) per rule | Choice of supporting studies | Clinical reviewer |
| Validation of MedlinePlus drafts | Clinical review of the educational text | Clinical reviewer |

## Database state

- Azure SQL: migrations applied through `0032_service_requires_order` (production, Medilo).
- Supabase: replica migrations `0001` to `0012` in `migrations_supabase`.
- Test database `MedicalBooking_test` is recreated from zero at the start of each test session. The
  test suite runs on the local SQL Server, never on the cloud database.

## Decisions taken (short list)

- Results are classified only against a validated reference applicable to the patient. When context
  is missing the category is `NO_EVALUABLE`; no category is invented.
- Published knowledge is immutable. Imports and edits create drafts; a doctor with
  `conocimiento.validar` publishes them.
- External sources never write clinical decisions. They provide terminology, educational text,
  evidence identity and hypothesis codes, each with its provenance.
- A denied access is audited with its outcome, including a missing permission.
- Missing and foreign resources answer the same 403, so identifiers do not leak.
- Replication uses the transactional outbox only. Kafka was discarded.
- Azure Load Balancer was chosen over Traffic Manager. TLS certificates are issued with DNS-01 validation
  through DuckDNS, because HTTP-01 does not work behind a load balancer with two VMs.
- Migrations run once per release from VM1, never from replicas.
- Health probes: `/health/live` does not touch the database, so a SQL outage does not remove every replica.
