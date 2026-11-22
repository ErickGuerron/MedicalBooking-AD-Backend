Objective: give the patient the doctor's contact data they need, and give the administrator a dedicated listing for cancellation approvals that shows only what that screen needs.
Branch: feature/cancellation-candidates (from develop). Delivery: ask-on-risk. Test runner: uv run pytest -m "not integration" (integration needs SQL Server localhost,1433). Risk: item 2 (who sees patient and doctor data), item 3 (public contract: GET /appointments loses the admin branch and gains a medico block; new route).

## Specs
- S1: User decision: the patient needs the doctor's contact data to communicate. GET /api/v1/appointments, patient branch only: each row gains a medico object with id, nombre, registro_profesional and telefono, for the doctor of that appointment. ASSUMPTION: a patient sees only the doctors of their own appointments (the ownership filter already applies). No other fields are added.
- S2: User decision (option confirmed): a dedicated listing for administrators, GET /api/v1/appointments/cancellation-approvals, requires citas.revisar_cancelaciones. Items carry id, estado, inicio, fin, tiene_aprobacion and medico {id, nombre, registro_profesional}. They never carry a patient and never carry the doctor's phone.
- S3: The new listing takes the optional query estado (validated, 422 otherwise) and medico_registro (exact match, case-insensitive; blank means no filter), plus limit and offset with the same rules as GET /appointments (default 50, max 200). Access is checked before paging, as in the rest of the module.
- S4: GET /api/v1/appointments loses the administrative branch. A user with citas.revisar_cancelaciones and no patient or doctor profile gets 403. The administrator's cancellation review uses S2.
- S5: Every read of the new listing writes one auditoria_accesos row: entidad "cita", no entidad_id, accion "listar_revision_cancelacion", resultado OK. A denied call writes resultado DENEGADO.
- S6: Doctor agenda and patient ownership behavior are unchanged. The doctor's agenda still names the patient of each row, as before.
- S7: The OpenAPI fragment of the scheduling module documents both changes; docs/api/openapi.json is regenerated; the README endpoint table lists the new route.
- S8: Frontend (AD-Medical-front-angular, separate repository): the cancellation approval screen uses S2. It no longer shows or searches patient names or doctor phones. Flutter (patient portal) is not changed in this feature; its use of S1 is a documented follow-up.

## Tasks
- T1 | S1 | inline | pending | patient branch carries the medico block (SQL join, mapping, unit tests first)
- T2 | S2, S3, S5 | inline | pending | store query, use case with audit, route
- T3 | S4 | inline | pending | remove the admin branch and its store method
- T4 | S7 | inline | pending | OpenAPI fragment, docs/api/openapi.json, README
- T5 | S8 | separate repo | pending | Angular adoption (see the front feature record)

## Log
- L1 (user, original request, verbatim): "pregunta actualmente el endpoint de appointments hay uno especial apra admin o es el general proque si es el general que tambien utiliza medico u paciente entonces hay si conviene hacer otro endpoint mas que todo porque 1. el medico necesita saber el nombre del paciente para poder consultar su perfil clinico, 2. el paciente necesita saber el numero del medco apra comunicarse con el"
- L2 (user, decision, verbatim): "1. si , 2. lo confirmo y adapta el front manteniendo el mismo estilo para que se adapte a los nuevos cambios"
- L3 (parent, evidence): the patient branch selects only id, estado, inicio and fin (_SUMMARY_SELECT). The doctor's agenda already returns the patient id and name, so item 1 needs no change. Item 2 is a real gap.
