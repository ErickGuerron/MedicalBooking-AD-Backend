Objective: E1 of the backend plan: identity, roles, permissions and authorization (401/403 correct, ownership enforced), with synthetic data.
Branch: develop. Delivery: ask-on-risk. Test runner: uv run pytest (integration needs SQL Server localhost,1433).

## Specs
- S1: E1 acceptance (spec section 11): "Matriz de permisos probada, 401/403 correctos y ownership".
- S2: JWT authentication; passwords hashed with Argon2id; short-lived tokens (spec section 10).
- S3: Authorization by role AND by patient/resource: patient reads own data; clinician reads only with an active, unexpired, unrevoked grant in accesos_paciente (spec 5.9, 10).
- S4: Credentials are not in the DBML. Added table credenciales_usuario (ADDITION, needs confirmation). usuarios stays provider-agnostic.
- S5: Synthetic data only: fictitious accounts on the .test domain; no passwords stored in the repository (spec 1, 5.9).
- S6: Use cases hold the rules; routes and SQL do not (spec 3 hexagonal layout).
- S7: Administrative user creation requires usuarios.administrar. MEDICO accounts are not creatable through the API yet (they need a professional registration flow).

## Tasks
- T1 | S4 | inline | done | migration 0003: credenciales_usuario + permission catalog (11) + role matrix (11 grants).
- T2 | S2, S6 | inline | done | identity domain policy (pure), ports, use cases, Argon2id hasher, HS256 JWT issuer.
- T3 | S3 | inline | done | SQL adapter with parameterized queries; GUID canonicalization; permissions read per request.
- T4 | S1, S3, S7 | inline | done | routes: POST /api/v1/auth/login, GET /api/v1/me, GET /api/v1/patients/{id}, POST /api/v1/users.
- T5 | S5 | inline | done | scripts/seed_synthetic.py: idempotent; refuses production; rotates synthetic passwords from SEED_PASSWORD.
- T6 | S1 | inline | done | tests: pure policy matrix, service with fakes, JWT and hasher attack cases, end-to-end matrix against SQL Server.
- T7 | S2 | done | migration 0004 (failed-login counter), policy failure_outcome, atomic SQL update, 5 failures lock 15 minutes. Unit (lockout) and integration (test_identity_lockout_api) tests. Per-IP rate limiting is not implemented: it needs a gateway or Nginx limit_req, not app state.
- T8 | S7 | pending | not started | MEDICO creation with registro_profesional (needs a decision on who registers doctors).
- T9 | S3 | pending | not started | ownership for laboratorista and resultados (resources arrive in E3).

## Log
- L1: "ya continuemos implementando las partes del backend" (user request).
- L2 (defect found by integration test): load_access left a lazy ODBC result open ("Connection is busy"). Fixed with .all().
- L3 (defect found by integration test): SQL Server returns GUIDs in upper case, tokens carry lower case, so the owner check always failed. Fixed by canonicalizing UUIDs in the adapter.
- L4 (evidence): 71 tests pass, coverage 98%; ruff clean; pip-audit: No known vulnerabilities; docker build succeeds and argon2/pyjwt import in the image.
- L5 (evidence): migration 0003 applied to MedicalBooking: 11 permissions, 11 role grants. Synthetic accounts exist (5). Login 200, /me returns PACIENTE, wrong password 401 (verified with a rotated password that is not recorded here).
- L6: The synthetic password must be chosen by the developer. Run: SEED_PASSWORD='<12+ chars>' uv run python scripts/seed_synthetic.py
