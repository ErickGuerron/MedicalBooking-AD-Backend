# Doctor cancellation limits

Objective: limit how often a doctor cancels booked appointments, and route exceptions to an admin. Branch: feature/doctor-cancel-limits (stacked on feature/refund-flow). Delivery: ask-on-risk. Test runner: uv run pytest.

## Specs

S1. A doctor may cancel at most 3 appointments of their own agenda per calendar month (UTC). Cancellations with motivo_codigo IMPREVISTO_MEDICO or OTRO_MEDICO count. Patient cancellations never count. A cancellation beyond the quota is refused with the stable code LIMITE_CANCELACION_MEDICO until an admin approves it for that appointment.
S2. A doctor cancellation made less than 24 hours before the slot start (horarios_disponibles.inicio_utc) is refused with CANCELACION_MEDICO_FUERA_DE_PLAZO until an admin approves it for that appointment.
S3. An admin with permission citas.revisar_cancelaciones records an approval for one appointment with a non-blank motivo. One approval per appointment; a second approval for the same appointment is refused with 409. An approval is consumed by the cancellation it allows and does not change the quota count.
S4. Patient cancellations, refund behavior, and the existing 1500-character reason rules are unchanged. The patient refund stays automatic and never waits for approval.
S5. The approval is not replicated to Supabase in this feature.

Assumptions (not user-stated): "calendar month" means UTC month; the clinic time zone is not modeled. Approval is required before the cancellation, not after.

## Tasks

T1 (S1, S2): domain policy for quota and notice. Done: RED (import error observed), GREEN 9 passed in tests/unit/test_cancel_limits.py. Route: inline.
T2 (S1, S2): store enforcement inside the cancel transaction; the doctor row is locked (UPDLOCK) so concurrent cancellations cannot both pass the quota. Done in SqlSchedulingStore._doctor_blockers. Route: inline.
T3 (S3): migration 0027 (aprobaciones_cancelacion_medico, unique per cita, permission citas.revisar_cancelaciones, ADMINISTRADOR matrix). Done in migrations/sql/0027 and versions/0027. Route: inline.
T4 (S3): admin endpoint POST /api/v1/appointments/{id}/cancellation-approval with OpenAPI entry and contract test. Done. Route: inline.
T5 (S1-S4): integration tests (quota block, admin exception, 24h block, patient not counted, duplicate approval, reason required, admin-only, unknown appointment). Done: 8 passed. Note: integration tests were written after the implementation, so they have no RED phase.
T6: help text and docs. The API documentation is the OpenAPI entry; no CLI help is involved.

Risk: item 2 (security: permissions and admin approval guard) and item 1 (new table, no destructive change). Verifier after own checks.

## Log

L1 (user, 2026-11-13, verbatim): "tamvez el admin u algun rol con mas autoridad lo revise y apruebe el motivo o no."
L2: plan approved by user: 3 cancellations per calendar month, 24-hour notice, over-quota refused until admin approval, admin permission citas.revisar_cancelaciones.
