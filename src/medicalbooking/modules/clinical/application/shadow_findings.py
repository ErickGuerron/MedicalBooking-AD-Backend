"""Shadow findings for one order (feature doc S13, S14). Read-only: nothing is stored or changed.

The ordering doctor reads the three findings next to the normal flow. The report states that it does
not modify the order, the result or the diagnosis record. Every read and every denial is audited.
Inputs come from the order's results, mapped by verified LOINC code (domain/loinc_roles.py).
Pregnancy is not in the patient profile yet, so women of reproductive age are not evaluable for
anaemia and iron deficiency until that field exists.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from medicalbooking.modules.clinical.application.ports import ClinicalStore
from medicalbooking.modules.clinical.domain.loinc_roles import AmbiguousResult, roles_from_loinc
from medicalbooking.modules.clinical.domain.shadow import ShadowInputs, ShadowReport, shadow_report
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.shared.domain.errors import Conflict, Forbidden

PERM_READ = "resultados.leer_con_acceso"
_FORBIDDEN = "Access to this resource is not allowed"
_SEXES = {"M", "F"}


class ShadowFindingsService:
    def __init__(self, store: ClinicalStore, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock

    def get(self, actor: AccessProfile, order_id: str) -> ShadowReport:
        if PERM_READ not in actor.permissions:
            self._audit(actor, None, order_id, "DENEGADO")
            raise Forbidden(_FORBIDDEN)
        order = self._store.load_order_for_evaluation(order_id)
        if order is None or order.medico_user_id != actor.user_id:
            self._audit(actor, order.paciente_id if order else None, order_id, "DENEGADO")
            raise Forbidden(_FORBIDDEN)

        try:
            roles = roles_from_loinc(self._store.loinc_results_for_order(order_id))
        except AmbiguousResult as error:
            raise Conflict(str(error)) from error

        sex, birth = self._store.patient_sex_birth(order.paciente_id)
        today = self._clock().date()
        report = shadow_report(_inputs(roles, sex, birth, today))
        self._audit(actor, order.paciente_id, order_id, "OK")
        return report

    def _audit(
        self, actor: AccessProfile, paciente_id: str | None, order_id: str, resultado: str
    ) -> None:
        self._store.record_audit(
            AuditEvent(
                actor_user_id=actor.user_id,
                paciente_id=paciente_id,
                entidad="modo_sombra",
                entidad_id=order_id,
                accion="consultar",
                resultado=resultado,
                occurred_at=self._clock(),
            )
        )


def _age_months(birth: date | None, today: date) -> int | None:
    if birth is None:
        return None
    months = (today.year - birth.year) * 12 + (today.month - birth.month)
    return months - 1 if today.day < birth.day else months


def _inputs(
    roles: dict[str, Decimal], sex: str | None, birth: date | None, today: date
) -> ShadowInputs:
    fasting = roles.get("glucosa_ayuno")
    hba1c = roles.get("hba1c")
    return ShadowInputs(
        sex=sex if sex in _SEXES else None,
        age_months=_age_months(birth, today),
        pregnancy=None,
        hemoglobin_g_dl=roles.get("hemoglobina"),
        ferritin_ug_l=roles.get("ferritina"),
        crp_mg_l=roles.get("pcr"),
        fasting_mg_dl=[fasting] if fasting is not None else [],
        hba1c_pct=[hba1c] if hba1c is not None else [],
        ogtt_2h_mg_dl=[],
        random_mg_dl=None,
        symptoms=None,
    )
