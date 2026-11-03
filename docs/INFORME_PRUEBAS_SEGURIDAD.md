# Informe de pruebas: integración con backend real y seguridad

Fecha: 2026-11-03. Alcance: instancia **local** del backend (`MedicalBooking-AD-Backend`, rama `develop`) con SQL Server
2025 local (base nueva `MedicalBooking_sec`, datos sintéticos) y los dos frontends. No se probó Azure, Nginx ni HTTPS.
Los scripts de prueba no se guardaron en ningún repositorio.

## 1. Integración Flutter ↔ backend real

Flujo ejecutado en el navegador contra `node tools/serve_web.mjs` (proxy a `localhost:5000`):

| Paso | Resultado |
| --- | --- |
| Login `paciente1@synthetic.test` | OK (200), sesión iniciada |
| `/me` y rol PACIENTE | OK |
| Disponibilidad por día (hora de la sede UTC-5) | OK, los horarios 01:00–04:00 UTC se muestran 20:00–23:00 |
| Reservar con `Idempotency-Key` | OK (201), estado `PENDIENTE_PAGO`, retención 15 min |
| Cancelar cita | OK (200), el horario vuelve a `DISPONIBLE` en la base |

Incidencias encontradas y corregidas/anotadas:

- **Git Bash convierte `/api/v1` en `C:/Program Files/Git/api/v1`** al pasar `--dart-define`; todo fallaba con "No se pudo conectar". Solución: compilar con `MSYS_NO_PATHCONV=1` (documentado en README y AGENTS.md de Flutter; cambio sin commitear).
- El pie de página dice "Versión demo" también en modo API (menor, pendiente).
- Angular no se re-ejecutó en navegador contra el backend real en esta pasada (sí se probó antes contra un mock; los endpoints de administrador se ejercitaron por API).

## 2. Pruebas de seguridad (API, instancia local)

Resultado: **90 verificaciones con aserción, 85 pasan y 5 no**: 1 defecto real (hallazgo 1), 2 observaciones de diseño o por confirmar (hallazgos 3 y 6) y 2 falsos fallos de mi script (nombres de archivo). Las cabeceras (hallazgo 2) se midieron como informativas, no como aserción.

### Autenticación y sesión (35/35)

- Los 22 endpoints protegidos devuelven 401 sin token, con `Bearer` vacío, token basura y esquema `Basic`.
- JWT: `alg=none`, claims alterados (rol ADMINISTRADOR) y firma corrupta son rechazados (401).
- Claims: `iss`, `aud`, `iat`, `exp`, `jti`; vida del token 900 s (15 min).
- Roles: PACIENTE, LABORATORISTA y MEDICO no pueden crear usuarios; PACIENTE no lista ni crea analitos ni órdenes; ADMIN no valida referencias (solo médico); MEDICO no reserva citas.
- Bloqueo de cuenta: tras 5 contraseñas incorrectas la cuenta se bloquea (respuesta idéntica 401, sin revelar el bloqueo).
- Sin enumeración de usuarios: mismo cuerpo de error y misma latencia (≈2.1 s) para usuario existente e inexistente.
- Alta de usuarios: campos extra (`id`, `is_active`) → 422 (sin mass assignment); contraseña corta → 422; correo duplicado → 409; rol inexistente → 422.

### Autorización a nivel de objeto (BOLA/IDOR)

- Otro paciente no puede leer la orden ni sus resultados (403); un médico ajeno tampoco (403).
- Otro paciente no puede cancelar una cita ajena (403) ni descargar un PDF ajeno (403).
- Doble reserva del mismo horario → 409. Reintento con la misma `Idempotency-Key` → misma cita (200); misma clave con otro contenido → 409; sin clave → 422.

### Validación de entradas

- Inyección SQL en login, `desde`, rutas y parámetros: sin efecto (consultas parametrizadas); tipos JSON incorrectos, JSON mal formado y `form-urlencoded` → 422. Contraseña de 200 KB → 422.
- Subida de PDF: se rechazan HTML y ejecutable con extensión .pdf, content-type incorrecto, archivo vacío y 30 MB (413). Los pacientes no pueden subir. El nombre del archivo se sanea (`safe_filename`) y la descarga usa `documento-<uuid>.pdf` con `Content-Disposition: attachment`.
- Errores en formato problem+json con `correlation_id`, sin trazas ni rutas.

## 3. Hallazgos

| # | Severidad | Hallazgo | Evidencia | Recomendación |
| --- | --- | --- | --- | --- |
| 1 | Media | **500 con `medico_id` inválido** en `GET /availability` (excepción sin manejar). No es inyección, pero rompe el contrato y deja trazas en el log. | `scheduling/presentation/routes.py:85`: `uuid.UUID(medico)` sin `try`. Con `?medico_id=' OR 1=1--` → 500. | Validar y responder 422 (como ya se hace con `desde`/`hasta`). |
| 2 | Media | **Faltan cabeceras de seguridad** en todas las respuestas: `X-Content-Type-Options`, `Strict-Transport-Security`, `Content-Security-Policy`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control`. En particular el login y los PDF no llevan `Cache-Control: no-store`. | Respuestas de `/health/live` y `/auth/login`. | Agregarlas en Flask o en `deploy/nginx` (HSTS solo cuando exista HTTPS). |
| 3 | Baja/Diseño | Un médico **sin relación con el paciente** puede crear órdenes para él (solo exige `ordenes.crear` y perfil médico); la lectura sí queda restringida al médico que ordenó. | `medico2` creó orden para `paciente1` (201). | Confirmar con el equipo si es intencional; si no, exigir `accesos_paciente`. |
| 4 | Baja | Login cuesta ≈2 s por intento (Argon2id `m=64MiB,t=3,p=4`) y el bloqueo por 5 fallos permite **bloquear a un usuario conocido** (denegación dirigida). | Mediciones; bloqueo reproducido. | Mantener el límite de Nginx (5/min por IP) y considerar bloqueo progresivo por IP+cuenta; revisar el costo de Argon2 bajo carga. |
| 5 | Informativa | Cabecera `Server: Werkzeug/…` y OpenAPI público cuando `API_DOCS_ENABLED=true` (aquí activo para la prueba). | `/api/v1/openapi.json` → 200. | En producción, `API_DOCS_ENABLED=false` y ocultar el banner. |
| 6 | Informativa | Registrar resultados con cuerpo bien formado como paciente devolvió 422, no 403: la validación del cuerpo parece ir antes de la autorización; no se pudo confirmar con un cuerpo válido. | Sin cambios de datos. | Verificar el orden autenticación → autorización → validación. |

Falsos fallos de mi script (no son defectos): nombres de archivo con `../` o doble extensión se aceptan (201) porque el nombre se sanea y no se usa en disco.

## 4. Lo que no se probó

- Límites de Nginx (429 en login y API), balanceo y HTTPS: requieren `docker compose up` del stack local.
- Escáner automatizado (OWASP ZAP está disponible como imagen local), pruebas de carga y fuzzing extenso.
- Manipulación de JWT con clave conocida (expiración forzada), revocación de tokens (el backend no la tiene: la sesión vive 15 min), concurrencia de reservas bajo carga.
- Frontends: XSS en Angular/Flutter (ambos escapan por defecto), almacenamiento de sesión (`sessionStorage`) y redirecciones abiertas están cubiertos solo por pruebas unitarias.

## 5. Cómo repetirlo

```bash
cd MedicalBooking-AD-Backend
sqlcmd -E -S "localhost,1270" -C -Q "CREATE DATABASE MedicalBooking_sec"
export SQLSERVER_HOST=localhost SQLSERVER_PORT=1270 SQLSERVER_DATABASE=MedicalBooking_sec
uv run alembic upgrade head
SEED_PASSWORD='<12+ caracteres>' uv run python scripts/seed_synthetic.py
JWT_SIGNING_KEY=$(openssl rand -hex 32) API_DOCS_ENABLED=true uv run flask --app medicalbooking.app:create_app run --port 5000
```

Los datos de catálogo (sede, servicio, agenda, horarios) se insertaron a mano en esa base; el repositorio no tiene seed para ellos.
