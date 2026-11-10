# Deployment topology: four Flask replicas on two VMs

Four identical, stateless API replicas run from one image. Two replicas run on each
Azure VM behind that VM's Nginx. The Azure Load Balancer spreads traffic across the
two VMs.

```text
Clients (Flutter, Angular)
        |
   Azure Load Balancer  (HTTP probe: GET /health/live on port 80)
     |                                   |
  VM1 (Nginx :80)                     VM2 (Nginx :80)
   |-- replica-a  api-1 (gunicorn)       |-- replica-a  api-3
   '-- replica-b  api-2 (gunicorn)       '-- replica-b  api-4
        |                                   |
        '------------ Azure SQL -------------'
```

| Path | File | Purpose |
|---|---|---|
| Image | `Dockerfile` | One artifact for all replicas and the migrate job |
| VM1 | `deploy/vm1/compose.yaml` | replicas api-1, api-2, Nginx, migrate profile |
| VM2 | `deploy/vm2/compose.yaml` | replicas api-3, api-4, Nginx, migrate profile |
| Nginx per VM | `deploy/nginx/vm.conf` | balances the two local replicas |
| Local simulation | `compose.yaml` | four replicas and one balancer on one host (port 18080) |
| Local Nginx | `deploy/nginx/local.conf` | balances the four local replicas |

## Design decisions

- **Probe on `/health/live`.** The Azure probe ignores the database. If it used
  `/health/ready`, a SQL Server outage would remove all four replicas at once, and
  the planned Supabase read fallback would have no entry point.
- **Migrations run once, outside replicas.** `migrate` is a Compose profile service.
  Run it from VM1 only, before starting replicas on a new release.
- **Retries never resend a POST that reached a replica.** Nginx `proxy_next_upstream`
  does not include `non_idempotent`, so a timeout cannot duplicate a reservation or
  payment. Idempotency keys still protect the business operations.
- **Rate limits at the edge.** Nginx limits login to 5 attempts per minute and the API to
  20 requests per second per client, returning `429` problem+json. The limit lives in Nginx
  rather than in the app because the four replicas do not share memory. The Azure Load
  Balancer (standard SKU) keeps the client address, so the limit keys on the real client.
  The health probe is well below the limit. Nginx configuration is validated in CI.
- **Least privilege in containers.** Read-only root filesystem, `/tmp` as tmpfs,
  all capabilities dropped, `no-new-privileges`, non-root user.
- **Replica identity.** `REPLICA_ID` is echoed in `X-Replica-Id` on every response.
  This shows the distribution in tests and in the evidence required by the spec.
- **Secrets.** Each VM has its own `.env` (git-ignored). Only `MEDICALBOOKING_IMAGE`
  is required in the Compose files.

## Deploy a VM

On each VM, with Docker installed:

```bash
# 1. Publish the image from CI, then pull it on the VM (registry publish is not in CI yet).
export MEDICALBOOKING_IMAGE=<registry>/medicalbooking:<tag>

# 2. Place the VM's .env next to the repository (SQLSERVER_* for Azure SQL,
#    JWT_SIGNING_KEY, APP_ENV=production, SQLSERVER_TRUST_SERVER_CERTIFICATE=no).

# 3. VM1 only: migrate once per release.
docker compose -f deploy/vm1/compose.yaml run --rm migrate

# 4. Start replicas and Nginx on each VM.
docker compose -f deploy/vm1/compose.yaml up -d      # VM1
docker compose -f deploy/vm2/compose.yaml up -d      # VM2
```

Lab PDFs are stored under `LAB_DOCUMENTS_DIR`, which the image sets to `/app/data/documents`. Mount a
persistent volume there on each VM (`docker run -v <name>:/app/data/documents ...`), or the files are lost
when the container is recreated. Until shared storage exists (see "Not yet implemented"), each VM keeps its
own files.

Run the knowledge importer on demand, not on a schedule: it needs clinical review of its output before
any draft is published. Command and guarantees: `docs/knowledge-import.md`.

Schedule the hold expiration worker (E4) on one VM, every few minutes, against the same
database. It is a one-shot process, not part of the replicas:

```bash
docker compose -f deploy/vm1/compose.yaml run --rm --entrypoint python migrate -m medicalbooking.workers.expire_holds
```

Then point the Azure Load Balancer backend pool at both VMs, port 80, with an HTTP
probe on `/health/live`. HTTPS with a DuckDNS name and Let's Encrypt is a later step
(E7); it is not configured yet.

## Local verification

```powershell
docker compose up -d --build
docker compose --profile migrate run --rm migrate   # needs SQLSERVER_* in .env
1..12 | ForEach-Object { (Invoke-WebRequest -UseBasicParsing http://localhost:18080/health/live).Headers["X-Replica-Id"] }
docker compose stop replica-2                        # failover: requests keep returning 200
docker compose start replica-2
```

Local SQL Server runs on the host, so containers reach it through `host.docker.internal`.
Containers cannot use Windows authentication, so `.env` must hold a SQL login.

Verified on this machine: the four replicas share 12 requests evenly (3 each). With
`replica-2` stopped, 20 of 20 requests returned `200`. `/health/ready` returned `200`
with `database: up` through the balancer.

## Experiment plan (spec section 12)

| Test | How | Expected |
|---|---|---|
| 1 vs 4 replicas | `docker compose stop` for three replicas, then load with a fixed number of parallel requests | Throughput and latency recorded for each configuration |
| API down | stop one replica during load | No failed requests or only retried ones |
| VM down | stop one VM's Nginx or power off the VM during load | Azure LB removes it; the other VM serves |
| Azure SQL down | block SQL during load | Liveness stays 200, readiness 503, no proposals created |

Results are not yet measured. These will be recorded in `docs/experiments/`.

## Not yet implemented

- **Shared document storage (blocks multi-VM deployment).** Uploaded lab PDFs are written to
  `LAB_DOCUMENTS_DIR` on the local disk of the host that handles the upload. Replicas on
  VM1 and VM2 do not share that disk, so a PDF uploaded through one VM is not found by a
  download routed to the other. Move storage to Azure Blob or a shared volume before E7.
- Publishing the image to a registry from CI.
- HTTPS, DuckDNS, and Let's Encrypt on the VMs.
- Azure resources, which are not provisioned from this repository.
- Replica load tests and failure experiments.
