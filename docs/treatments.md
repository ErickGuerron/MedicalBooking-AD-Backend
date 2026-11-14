# Treatments

A treatment is a structured record linked to one appointment (`cita`). Only the doctor who owns
the appointment creates and changes it. The owning patient can read it. Follow-up notes are
appended to a treatment and are never edited or deleted through the API.

The free-text `plan_tratamiento` field of `consultas_medicas` is a separate summary. The
treatment endpoints neither read nor write it.

All endpoints live under `/api/v1`, require a Bearer token, and use JSON. Errors follow the
Problem format used by the rest of the API.

## Endpoints

| Method | Path | Who | Result |
|---|---|---|---|
| POST | `/citas/{cita_id}/tratamientos` | treating doctor | 201 treatment |
| GET | `/citas/{cita_id}/tratamientos` | treating doctor or owning patient | 200 `items` |
| PATCH | `/tratamientos/{tratamiento_id}` | treating doctor | 200 treatment |
| POST | `/tratamientos/{tratamiento_id}/seguimientos` | treating doctor | 201 follow-up |
| GET | `/tratamientos/{tratamiento_id}/seguimientos` | treating doctor or owning patient | 200 `items` |

A missing resource and a resource that belongs to someone else both answer `403`.
Denied attempts are audited with `resultado = DENEGADO`.

### Treatment body

Create (`POST`):

```json
{ "descripcion": "Reposo y control en 7 dias", "fecha_inicio": "2026-11-02",
  "estado": "ACTIVO", "fecha_fin": null }
```

- `descripcion`: required, 1 to 1000 characters, not blank.
- `fecha_inicio`: required, ISO date.
- `estado`: optional, default `ACTIVO`. One of `ACTIVO`, `FINALIZADO`, `SUSPENDIDO`.
- `fecha_fin`: optional ISO date. Required when `estado` is `FINALIZADO`. Cannot be before
  `fecha_inicio`.

Update (`PATCH`): any subset of `descripcion`, `estado`, `fecha_fin`. An empty body is `422`.

State changes:

- `ACTIVO` to `SUSPENDIDO` or `FINALIZADO`.
- `SUSPENDIDO` to `ACTIVO` or `FINALIZADO`.
- `FINALIZADO` is terminal. Any change out of it is `422`.
- A concurrent state change that loses the race answers `409`.

Unknown fields are rejected with `422`, including `plan_tratamiento`.

### Follow-up body

```json
{ "nota": "Evolucion favorable" }
```

`nota` is required, 1 to 2000 characters, not blank. There is no PATCH or DELETE for
follow-ups: the database also refuses UPDATE and DELETE on them.

## Storage and audit

- Tables `tratamientos` and `tratamientos_seguimientos` (migration `0029_treatments`).
- Follow-ups are ordered by an insertion sequence, so notes registered at the same instant keep
  their order.
- Each read and write writes one `auditoria_accesos` row with entity, action and result. The
  row never contains the description or the note.
- Treatments and follow-ups are replicated with their full text (`descripcion` and `nota`) through
  the outbox and Kafka into the Supabase replica and MongoDB. This is an explicit product decision
  (option A). Audit rows still exclude these values.

## Permissions

No new permission was added. The treating doctor uses `revisiones.registrar` for writes, and
doctors read with `resultados.leer_con_acceso`. Patients read with `resultados.leer_propios`.
The access rule is still per appointment, so the permission only gates the role.
