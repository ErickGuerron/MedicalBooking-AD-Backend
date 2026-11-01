Objective: four stateless Flask replicas distributed over two Azure VMs (two per VM) behind Nginx and an Azure Load Balancer; add dependency audit to CI.
Branch: develop (merge target main). Delivery: ask-on-risk. Test runner: uv run pytest; local stack: docker compose up -d --build.

## Specs
- S1: "sigue implementando las 4 instancias de flask que pide y como se distribuirian en 2 maquians virtuales distintas" (user request).
- S2: Four identical replicas from one image: VM1 runs api-1 and api-2, VM2 runs api-3 and api-4 (proyecto_final_integrador.md section 7; spec section 2).
- S3: Each VM runs Nginx in front of its two replicas; Azure Load Balancer distributes between the two VMs (spec 11 E7).
- S4: Replicas are stateless; no in-memory locks; database enforces concurrency (spec 2).
- S5: The Azure probe must not depend on the database (liveness), so a SQL outage does not remove all replicas (design decision, spec 6 and 8.3).
- S6: Migrations run once per release, never from replicas (design decision).
- S7: Add pip-audit to CI and fail on known vulnerabilities (user request, spec 10).

## Tasks
- T1 | S7 | inline | done | pip-audit dev dependency; pytest raised to 9.2 because PYSEC-2026-1845 affected 8.4.2 (fix 9.0.3); CI job "Dependency audit".
- T2 | S4 | inline | done | X-Replica-Id response header from REPLICA_ID setting (validated pattern). Tests: tests/unit/test_replica.py.
- T3 | S2, S3 | inline | done | deploy/nginx/vm.conf and deploy/nginx/local.conf (proxy_next_upstream without non_idempotent).
- T4 | S2, S5, S6 | inline | done | deploy/vm1/compose.yaml, deploy/vm2/compose.yaml, root compose.yaml (4 replicas + balancer on 18080).
- T5 | S1 | inline | done | docs/deployment.md with topology, decisions, procedures, experiment plan.
- T6 | S7, S2 | inline | done | CI jobs: audit (uv export + pip-audit --strict), deploy-config (compose config and nginx -t); docker job needs all checks.
- T7 | S3 | pending | not started | Azure LB configuration, VM provisioning, HTTPS and DuckDNS (E7, needs user authorization and Azure resources).
- T8 | S1 | pending | not started | registry publish of the image from CI (needed for VM pulls).
- T9 | spec 12 | pending | not started | load and failure experiments with measured results (docs/experiments/).

## Log
- L1: "solo quiero que agregues el pip-audit y sigue implementando las 4 instancias de flask que pide y como se distribuirian en 2 maquians virtuales distintas"
- L2 (evidence): pip-audit --locked does not accept uv.lock in pip-audit 2.10.1; used uv export then pip-audit -r. Result: No known vulnerabilities after pytest upgrade.
- L3 (evidence): local stack. 4 replicas healthy; 12 requests distributed 3/3/3/3; with replica-2 stopped 20/20 returned 200; /health/ready 200 (database up via .env SQL login).
- L4 (evidence): docker compose config validates all three files; nginx -t successful for both configs with the CI add-host mapping.
- L5: Port 8080 is blocked on this machine (socket permission); local balancer uses 18080.
- L6: Local .env holds a SQL login; containers cannot use Windows authentication. Documented in compose.yaml and docs/deployment.md.
